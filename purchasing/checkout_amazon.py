"""
IMQ2 Purchasing — Amazon.ca Checkout Automation
Handles the full purchase flow for amazon.ca using Q2's account
(iamkewtoo@gmail.com) with gift card balance as the payment method.

Key differences from RotorVillage:
  - Payment: gift card account balance applied automatically — no card entry
  - Bot detection: Amazon is significantly more aggressive than RotorVillage.
    We use slow_mo, human-paced delays, and headed mode to stay under the radar.
  - Login: stored credentials in .env (AMAZON_EMAIL / AMAZON_PASSWORD)
  - Shipping: Amazon uses its own address book — address must be saved to
    the iamkewtoo account before automation runs.

Amazon.ca gift card balance policy:
  If the account balance covers the full order total (including tax + shipping),
  Amazon applies it automatically and no additional payment method is needed.
  The automation confirms the balance checkbox is ticked before placing the order.
"""

import asyncio
import logging
import os
import random
import re
import time
from pathlib import Path
from typing import Optional

log = logging.getLogger(__name__)


class AmazonCheckout:
    """
    Drives a visible Playwright browser through Amazon.ca checkout.
    Instantiate once per purchase attempt; do not reuse.
    """

    def __init__(self, config, ledger):
        from dotenv import load_dotenv
        load_dotenv(Path(__file__).parent.parent / ".env")

        self._config   = config
        self._ledger   = ledger
        self._email    = os.environ.get("AMAZON_EMAIL", "")
        self._password = os.environ.get("AMAZON_PASSWORD", "")
        self._browser  = None
        self._page     = None
        self._pw       = None

    # ------------------------------------------------------------------
    # Public entry point
    # ------------------------------------------------------------------

    async def execute(self, product_url: str, item_description: str,
                      confirm_callback) -> dict:
        """
        Full purchase flow. confirm_callback(summary: str) -> bool is called
        with a human-readable summary before any order is placed.
        Returns {"ok": bool, "message": str, "order_id": str|None}
        """
        if not self._email or not self._password:
            return {"ok": False, "message": "AMAZON_EMAIL / AMAZON_PASSWORD not set in .env.", "order_id": None}

        # Pre-flight balance check
        cards = self._ledger.list_gift_cards()
        amazon_cards = [c for c in cards if c.get("payment_type") == "account_balance" and c["remaining"] > 0]
        if not amazon_cards:
            return {"ok": False, "message": "No Amazon account balance card in ledger. Add one via the settings panel (type: Account balance).", "order_id": None}

        balance_card = amazon_cards[0]

        try:
            from playwright.async_api import async_playwright

            self._pw = await async_playwright().start()
            self._browser = await self._pw.chromium.launch(
                headless=False,
                slow_mo=600,  # deliberately slow — Amazon's behavioral analysis
                              # flags instant clicks as bot-like
                args=["--start-maximized", "--disable-blink-features=AutomationControlled"],
            )
            ctx = await self._browser.new_context(
                viewport={"width": 1366, "height": 768},
                locale="en-CA",
                timezone_id="America/Toronto",
                user_agent=(
                    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
                    "(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36"
                ),
            )
            # Remove the webdriver property that Amazon checks for
            await ctx.add_init_script(
                "Object.defineProperty(navigator, 'webdriver', {get: () => undefined})"
            )
            self._page = await ctx.new_page()

            # Step 1: Login
            log.info("Amazon checkout: logging in...")
            ok, msg = await self._login()
            if not ok:
                return {"ok": False, "message": f"Login failed: {msg}", "order_id": None}

            # Step 2: Clear cart
            await self._clear_cart()

            # Step 3: Navigate to product and add to cart
            log.info(f"Amazon checkout: adding product {product_url}")
            ok, msg = await self._add_to_cart(product_url)
            if not ok:
                return {"ok": False, "message": f"Could not add to cart: {msg}", "order_id": None}

            # Step 4: Proceed to checkout
            log.info("Amazon checkout: proceeding to checkout...")
            ok, msg = await self._proceed_to_checkout()
            if not ok:
                return {"ok": False, "message": f"Could not reach checkout: {msg}", "order_id": None}

            # Step 5: Confirm shipping address
            log.info("Amazon checkout: confirming shipping address...")
            await self._confirm_shipping_address()

            # Step 6: Select shipping method
            log.info("Amazon checkout: selecting shipping...")
            shipping_name, shipping_cost = await self._select_shipping()

            # Step 7: Threshold check
            confirm_threshold = self._config.get("purchasing.shipping.confirm_above_cad", 25.0)
            if shipping_cost > confirm_threshold:
                approved = await confirm_callback(
                    f"Shipping cost is ${shipping_cost:.2f} ({shipping_name}), "
                    f"which exceeds your ${confirm_threshold:.2f} threshold. Proceed?"
                )
                if not approved:
                    return {"ok": False, "message": "Purchase rejected — shipping above threshold.", "order_id": None}

            # Step 8: Confirm gift card balance is applied
            log.info("Amazon checkout: confirming gift card balance is applied...")
            await self._confirm_balance_applied()

            # Step 9: Read order total
            order_total, item_total = await self._read_order_total()

            # Step 10: Check balance covers it
            if order_total > balance_card["remaining"]:
                return {
                    "ok": False,
                    "message": (
                        f"Order total ${order_total:.2f} exceeds available "
                        f"Amazon balance ${balance_card['remaining']:.2f}. "
                        f"Add more funds to the iamkewtoo Amazon account."
                    ),
                    "order_id": None,
                }

            # Step 11: Final confirmation
            summary = (
                f"Order summary:\n"
                f"  Item: {item_description}\n"
                f"  Shipping: {shipping_name} — ${shipping_cost:.2f}\n"
                f"  Item total: ${item_total:.2f}\n"
                f"  Order total: ${order_total:.2f}\n"
                f"  Payment: Amazon account balance (${balance_card['remaining']:.2f} available)\n"
                f"\nShall I place this order?"
            )
            approved = await confirm_callback(summary)
            if not approved:
                return {"ok": False, "message": "Purchase rejected at final confirmation.", "order_id": None}

            # Step 12: Record in ledger
            purchase_id = self._ledger.record_purchase_request(
                item_description=item_description,
                merchant="amazon.ca",
                amount=order_total,
            )
            self._ledger.mark_confirmed(purchase_id)

            # Step 13: Place order
            log.info("Amazon checkout: placing order...")
            ok, order_id, msg = await self._place_order()

            if ok:
                self._ledger.mark_completed(purchase_id, gift_card_id=balance_card["id"],
                                            notes=f"Order {order_id}")
                return {"ok": True, "message": f"Order placed. {msg}", "order_id": order_id}
            else:
                self._ledger.mark_failed(purchase_id, notes=msg)
                return {"ok": False, "message": f"Order placement failed: {msg}", "order_id": None}

        except Exception as e:
            log.error(f"Amazon checkout: unexpected error: {e}", exc_info=True)
            return {"ok": False, "message": f"Unexpected error: {e}", "order_id": None}
        finally:
            if self._browser:
                try:
                    await self._browser.close()
                except Exception:
                    pass
            if self._pw:
                try:
                    await self._pw.stop()
                except Exception:
                    pass

    # ------------------------------------------------------------------
    # Step implementations
    # ------------------------------------------------------------------

    async def _human_delay(self, min_ms: int = 400, max_ms: int = 1200):
        """Random delay to mimic human hesitation."""
        await self._page.wait_for_timeout(random.randint(min_ms, max_ms))

    async def _login(self) -> tuple[bool, str]:
        await self._page.goto("https://www.amazon.ca/ap/signin?openid.pape.max_auth_age=0&openid.return_to=https%3A%2F%2Fwww.amazon.ca%2F%3Fref_%3Dnav_custrec_signin&openid.identity=http%3A%2F%2Fspecs.openid.net%2Fauth%2F2.0%2Fidentifier_select&openid.assoc_handle=caflex&openid.mode=checkid_setup&openid.claimed_id=http%3A%2F%2Fspecs.openid.net%2Fauth%2F2.0%2Fidentifier_select&openid.ns=http%3A%2F%2Fspecs.openid.net%2Fauth%2F2.0",
                              wait_until="domcontentloaded")
        await self._human_delay(600, 1400)

        try:
            # Email step
            await self._page.fill("#ap_email", self._email)
            await self._human_delay(300, 700)
            await self._page.click("#continue")
            await self._page.wait_for_timeout(2000)

            # Password step
            await self._page.fill("#ap_password", self._password)
            await self._human_delay(400, 900)
            await self._page.click("#signInSubmit")
            await self._page.wait_for_timeout(3000)

            # Check for OTP / CAPTCHA challenge
            if "auth-mfa" in self._page.url or "ap/cvf" in self._page.url:
                log.warning("Amazon: MFA or CAPTCHA challenge detected — manual intervention needed")
                print("\n⚠  Amazon is asking for verification. Complete it in the browser, then press ENTER.")
                input()
                await self._page.wait_for_timeout(2000)

            if "signin" in self._page.url and "amazon.ca" in self._page.url:
                return False, f"Still on sign-in page: {self._page.url}"

            log.info(f"Amazon: logged in, URL: {self._page.url}")
            return True, "OK"
        except Exception as e:
            return False, str(e)

    async def _clear_cart(self):
        """Empty the cart to avoid surprise multi-item orders."""
        try:
            await self._page.goto("https://www.amazon.ca/gp/cart/view.html", wait_until="domcontentloaded")
            await self._page.wait_for_timeout(2000)

            # Delete all items
            while True:
                delete_btns = await self._page.locator("input[value='Delete'], [data-action='delete']").all()
                if not delete_btns:
                    break
                await delete_btns[0].click()
                await self._page.wait_for_timeout(1500)
        except Exception as e:
            log.debug(f"Cart clear: {e}")

    async def _add_to_cart(self, product_url: str) -> tuple[bool, str]:
        await self._page.goto(product_url, wait_until="domcontentloaded")
        await self._human_delay(800, 1600)

        # Check for out-of-stock
        oos_indicators = [
            "text=Currently unavailable",
            "text=Out of Stock",
            "text=This item cannot be shipped",
        ]
        for ind in oos_indicators:
            try:
                if await self._page.locator(ind).count() > 0:
                    return False, f"Product unavailable: {ind}"
            except Exception:
                pass

        add_sels = [
            "#add-to-cart-button",
            "input[id='add-to-cart-button']",
            "button[name='submit.add-to-cart']",
        ]
        for sel in add_sels:
            try:
                btn = self._page.locator(sel).first
                if await btn.is_visible(timeout=3000):
                    await self._human_delay(300, 800)
                    await btn.click()
                    await self._page.wait_for_timeout(2500)

                    # Dismiss any "add to cart" confirmation modal if it appears
                    try:
                        modal_close = self._page.locator("#attach-sidesheet-checkout-button, .a-button-close").first
                        if await modal_close.is_visible(timeout=1000):
                            await modal_close.click()
                    except Exception:
                        pass

                    return True, "OK"
            except Exception:
                continue

        return False, "No add-to-cart button found."

    async def _proceed_to_checkout(self) -> tuple[bool, str]:
        await self._page.goto("https://www.amazon.ca/gp/cart/view.html", wait_until="domcontentloaded")
        await self._human_delay(800, 1500)

        checkout_sels = [
            "input[name='proceedToRetailCheckout']",
            "a:has-text('Proceed to checkout')",
            "#sc-buy-box-ptc-button input",
            ".sc-buy-box-ptc .a-button",
        ]
        for sel in checkout_sels:
            try:
                btn = self._page.locator(sel).first
                if await btn.is_visible(timeout=3000):
                    await self._human_delay(300, 700)
                    await btn.click()
                    await self._page.wait_for_timeout(4000)
                    if "checkout" in self._page.url.lower() or "place-order" in self._page.url.lower():
                        return True, "OK"
            except Exception:
                continue

        return False, f"Could not reach checkout. URL: {self._page.url}"

    async def _confirm_shipping_address(self):
        """Confirm the saved address is selected; if prompted to choose, pick the first one."""
        await self._human_delay(500, 1000)
        try:
            # If there's a "Use this address" or "Deliver to this address" button
            deliver_btn = self._page.locator("input[name='shipToThisAddress'], a:has-text('Deliver to this address')").first
            if await deliver_btn.is_visible(timeout=2000):
                await deliver_btn.click()
                await self._page.wait_for_timeout(2500)
        except Exception:
            pass

        # Click Continue/Next if present on address step
        try:
            continue_btn = self._page.locator("#continue-top, input[name='continue'], .shipping-continue button").first
            if await continue_btn.is_visible(timeout=2000):
                await continue_btn.click()
                await self._page.wait_for_timeout(2500)
        except Exception:
            pass

    async def _select_shipping(self) -> tuple[Optional[str], float]:
        """Select shipping per config priority list."""
        preferred = self._config.get(
            "purchasing.shipping.preferred_services",
            ["Canada Post Xpresspost", "Canada Post"]
        )
        await self._human_delay(500, 1000)

        # Amazon shipping options
        shipping_options = await self._page.locator(
            "input[name*='shipping'], .shipping-speed-option input[type='radio']"
        ).all()

        options = []
        for inp in shipping_options:
            try:
                inp_id = await inp.get_attribute("id") or ""
                label_text = ""
                if inp_id:
                    try:
                        label_text = await self._page.locator(f"label[for='{inp_id}']").first.inner_text(timeout=500)
                    except Exception:
                        pass
                cost_match = re.search(r'\$([0-9]+\.[0-9]+)', label_text)
                cost = float(cost_match.group(1)) if cost_match else 0.0
                # "FREE" shipping
                if "free" in label_text.lower():
                    cost = 0.0
                options.append({"input": inp, "label": label_text, "cost": cost})
                log.debug(f"Amazon shipping: {label_text.strip()!r} -> ${cost:.2f}")
            except Exception:
                continue

        if not options:
            log.warning("No shipping options found — may already be on payment step")
            return "Standard", 0.0

        # Try preferred services in priority order
        for preferred_name in preferred:
            for opt in options:
                if preferred_name.lower() in opt["label"].lower():
                    await opt["input"].click()
                    await self._human_delay(400, 800)
                    return opt["label"].strip(), opt["cost"]

        # Fallback
        fallback = self._config.get("purchasing.shipping.fallback", "cheapest")
        if fallback == "cheapest":
            cheapest = min(options, key=lambda o: o["cost"])
            await cheapest["input"].click()
            await self._human_delay(400, 800)
            return cheapest["label"].strip(), cheapest["cost"]

        # Continue to next step
        try:
            cont = self._page.locator("#continue-top, input[name='continue'], button:has-text('Continue')").first
            if await cont.is_visible(timeout=2000):
                await cont.click()
                await self._page.wait_for_timeout(2500)
        except Exception:
            pass

        return options[0]["label"].strip(), options[0]["cost"]

    async def _confirm_balance_applied(self):
        """
        Ensure the Amazon gift card balance checkbox is ticked.
        Amazon applies it by default but we verify it rather than assuming.
        """
        await self._human_delay(500, 1000)
        try:
            # Look for the gift card balance checkbox / section
            gc_sels = [
                "input[name*='giftCard'][type='checkbox']",
                "#gc-apply-input",
                "input[id*='gcBalance']",
            ]
            for sel in gc_sels:
                try:
                    cb = self._page.locator(sel).first
                    if await cb.is_visible(timeout=1500):
                        if not await cb.is_checked():
                            await cb.click()
                            await self._page.wait_for_timeout(1500)
                            log.info("Amazon: gift card balance checkbox ticked")
                        else:
                            log.info("Amazon: gift card balance already applied")
                        return
                except Exception:
                    continue
            log.debug("Amazon: no explicit gift card balance checkbox found — assuming auto-applied")
        except Exception as e:
            log.debug(f"Balance confirmation: {e}")

    async def _read_order_total(self) -> tuple[float, float]:
        """Read the order total and item subtotal from the checkout review page."""
        order_total = 0.0
        item_total  = 0.0
        await self._human_delay(300, 700)
        try:
            # Amazon's checkout summary uses data attributes and specific class names
            total_sels = [
                "#subtotals-marketplace-table .grand-total-price",
                "#subtotals-marketplace-table tr:last-child td:last-child",
                ".order-summary-total .a-text-bold",
                "#order-summary-total-amount",
            ]
            for sel in total_sels:
                try:
                    el = self._page.locator(sel).last
                    if await el.count() > 0:
                        text = await el.inner_text()
                        match = re.search(r'[\d,]+\.\d+', text.replace(",", ""))
                        if match:
                            order_total = float(match.group().replace(",", ""))
                            break
                except Exception:
                    continue

            subtotal_sels = [
                "#subtotals-marketplace-table tr:first-child td:last-child",
                ".subtotal-amount",
            ]
            for sel in subtotal_sels:
                try:
                    el = self._page.locator(sel).first
                    if await el.count() > 0:
                        text = await el.inner_text()
                        match = re.search(r'[\d,]+\.\d+', text.replace(",", ""))
                        if match:
                            item_total = float(match.group().replace(",", ""))
                            break
                except Exception:
                    continue

        except Exception as e:
            log.warning(f"Could not read Amazon order total: {e}")

        return order_total, item_total

    async def _place_order(self) -> tuple[bool, str, str]:
        """Click place order and extract the confirmation order number."""
        place_sels = [
            "#submitOrderButtonId input",
            "input[name='placeYourOrder1']",
            "button:has-text('Place your order')",
            "#placeOrder",
        ]
        for sel in place_sels:
            try:
                btn = self._page.locator(sel).first
                if await btn.is_visible(timeout=3000):
                    await self._human_delay(800, 1500)  # deliberate pause before the final click
                    await btn.click()
                    log.info("Amazon: place order clicked")
                    await self._page.wait_for_timeout(6000)

                    # Look for confirmation
                    confirm_sels = [
                        ".thank-you-message",
                        "h1:has-text('order')",
                        "[data-test-id='order-summary-header']",
                        "h4:has-text('Thank')",
                    ]
                    for csel in confirm_sels:
                        try:
                            if await self._page.locator(csel).count() > 0:
                                # Extract order number
                                order_id = ""
                                page_text = await self._page.inner_text("body")
                                num_match = re.search(r'(\d{3}-\d{7}-\d{7})', page_text)
                                if num_match:
                                    order_id = num_match.group(1)
                                return True, order_id, f"Order confirmed"
                        except Exception:
                            continue

                    if "checkout" not in self._page.url.lower():
                        return True, "", f"Order likely placed — now at: {self._page.url}"

                    return False, "", "Clicked place order but no confirmation detected"
            except Exception:
                continue

        return False, "", "Could not find place order button"


# ------------------------------------------------------------------
# Sync wrapper for the tool registry
# ------------------------------------------------------------------

def execute_amazon_purchase_sync(product_url: str, item_description: str,
                                 confirm_callback_sync, config, ledger) -> dict:
    async def async_confirm(summary: str) -> bool:
        return confirm_callback_sync(summary)

    checkout = AmazonCheckout(config=config, ledger=ledger)

    try:
        loop = asyncio.get_event_loop()
    except RuntimeError:
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)

    return loop.run_until_complete(
        checkout.execute(
            product_url=product_url,
            item_description=item_description,
            confirm_callback=async_confirm,
        )
    )

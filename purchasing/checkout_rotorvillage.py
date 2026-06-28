"""
IMQ2 Purchasing — RotorVillage Checkout Automation
Handles the full purchase flow for rotorvillage.ca:
  1. Log in with Q2's account credentials
  2. Search for and add a specific product to cart
  3. Proceed to checkout
  4. Select shipping per the priority list from config
  5. Check shipping cost against the confirmation threshold
  6. Apply gift card code from the ledger
  7. Present full order summary to the user for confirmation
  8. Only if confirmed: place the order
  9. Record the transaction in the budget ledger

This module is called by the execute_purchase tool in tools/registry.py.
It is never called automatically — always gated by explicit user confirmation.
"""

import asyncio
import logging
import os
import re
import time
from pathlib import Path
from typing import Optional

log = logging.getLogger(__name__)

# Sentinel values for the interactive confirmation flow
CONFIRMED = "confirmed"
REJECTED  = "rejected"
THRESHOLD_EXCEEDED = "threshold_exceeded"


class RotorVillageCheckout:
    """
    Drives a visible Playwright browser through the RotorVillage checkout.
    Instantiate once per purchase attempt; do not reuse across purchases.
    """

    def __init__(self, config, ledger):
        from dotenv import load_dotenv
        load_dotenv(Path(__file__).parent.parent / ".env")

        self._config  = config
        self._ledger  = ledger
        self._email    = os.environ.get("ROTORVILLAGE_EMAIL", "")
        self._password = os.environ.get("ROTORVILLAGE_PASSWORD", "")
        self._browser  = None
        self._page     = None
        self._playwright = None

    # ------------------------------------------------------------------
    # Public entry point
    # ------------------------------------------------------------------

    async def execute(
        self,
        product_url: str,
        item_description: str,
        confirm_callback,          # async callable(summary: str) -> bool
    ) -> dict:
        """
        Full purchase flow. confirm_callback is called with a human-readable
        order summary string; it should return True to proceed, False to abort.
        Returns a result dict: {"ok": bool, "message": str, "order_id": str|None}
        """
        if not self._email or not self._password:
            return {"ok": False, "message": "ROTORVILLAGE_EMAIL / ROTORVILLAGE_PASSWORD not set in .env.", "order_id": None}

        # Pre-flight budget check — will the ledger even allow this?
        per_cap = self._config.get("purchasing.per_purchase_cap", 50.0)
        available = self._ledger.total_available_balance()
        if available <= 0:
            return {"ok": False, "message": f"No gift card balance available (${available:.2f}).", "order_id": None}

        try:
            from playwright.async_api import async_playwright
            self._playwright = await async_playwright().start()
            self._browser = await self._playwright.chromium.launch(
                headless=False,
                slow_mo=350,
                args=["--start-maximized"],
            )
            ctx = await self._browser.new_context(
                viewport={"width": 1280, "height": 900},
                locale="en-CA",
            )
            self._page = await ctx.new_page()

            # Step 1: Login
            log.info("RV checkout: logging in...")
            ok, msg = await self._login()
            if not ok:
                return {"ok": False, "message": f"Login failed: {msg}", "order_id": None}

            # Step 2: Clear cart first (avoid surprises from previous sessions)
            await self._clear_cart()

            # Step 3: Add product
            log.info(f"RV checkout: adding product {product_url}")
            ok, msg = await self._add_to_cart(product_url)
            if not ok:
                return {"ok": False, "message": f"Could not add to cart: {msg}", "order_id": None}

            # Step 4: Proceed to checkout
            log.info("RV checkout: proceeding to checkout...")
            ok, msg = await self._proceed_to_checkout()
            if not ok:
                return {"ok": False, "message": f"Could not reach checkout: {msg}", "order_id": None}

            # Step 5: Select shipping
            log.info("RV checkout: selecting shipping...")
            selected_shipping, shipping_cost = await self._select_shipping()
            if selected_shipping is None:
                return {"ok": False, "message": "No suitable shipping option found.", "order_id": None}

            # Step 6: Threshold check — ask before gift card application
            confirm_threshold = self._config.get("purchasing.shipping.confirm_above_cad", 25.0)
            if shipping_cost > confirm_threshold:
                log.info(f"RV checkout: shipping ${shipping_cost:.2f} exceeds threshold ${confirm_threshold:.2f} — pausing for confirmation")
                approved = await confirm_callback(
                    f"Shipping cost is ${shipping_cost:.2f} ({selected_shipping}), "
                    f"which exceeds your ${confirm_threshold:.2f} threshold. Proceed?"
                )
                if not approved:
                    return {"ok": False, "message": "Purchase rejected — shipping cost above threshold.", "order_id": None}

            # Step 7: Get gift card from ledger
            cards = self._ledger.list_gift_cards()
            # For RotorVillage, prefer site_gc cards first, then visa_mc
            # account_balance cards aren't applicable here (those are for Amazon etc.)
            usable_cards = [
                c for c in cards
                if c["remaining"] > 0 and c.get("payment_type", "site_gc") in ("site_gc", "visa_mc")
            ]
            if not usable_cards:
                return {"ok": False, "message": "No applicable gift card found. Add a site gift card or Visa/MC prepaid card for RotorVillage.", "order_id": None}
            gift_card = usable_cards[0]

            # Retrieve full card code directly from DB
            gc_row = self._ledger._db.execute(
                "SELECT card_code, payment_type FROM gift_cards WHERE id = ?", (gift_card["id"],)
            ).fetchone()
            card_code    = gc_row[0] if gc_row else ""
            payment_type = gc_row[1] if gc_row else "site_gc"

            # Step 8: Apply payment
            if payment_type == "site_gc":
                if not card_code:
                    return {"ok": False, "message": f"Gift card '{gift_card['label']}' has no code stored.", "order_id": None}
                log.info(f"RV checkout: applying site gift card '{gift_card['label']}'...")
                ok, msg = await self._apply_gift_card(card_code)
                if not ok:
                    log.warning(f"Gift card application: {msg}")
            elif payment_type == "visa_mc":
                if not card_code:
                    return {"ok": False, "message": f"Visa/MC card '{gift_card['label']}' has no card number stored.", "order_id": None}
                log.info(f"RV checkout: entering Visa/MC card '{gift_card['label']}'...")
                ok, msg = await self._apply_visa_mc(card_code)
                if not ok:
                    return {"ok": False, "message": f"Could not enter card details: {msg}", "order_id": None}

            # Step 9: Read final order total
            order_total, item_total = await self._read_order_total()

            # Step 10: Final confirmation with full summary
            summary = (
                f"Order summary:\n"
                f"  Item: {item_description}\n"
                f"  Shipping: {selected_shipping} — ${shipping_cost:.2f}\n"
                f"  Item total: ${item_total:.2f}\n"
                f"  Order total: ${order_total:.2f}\n"
                f"  Payment: {gift_card['label']} (balance: ${gift_card['remaining']:.2f})\n"
                f"\nShall I place this order?"
            )
            log.info(f"RV checkout: presenting final confirmation:\n{summary}")
            approved = await confirm_callback(summary)

            if not approved:
                return {"ok": False, "message": "Purchase rejected by user at final confirmation.", "order_id": None}

            # Step 11: Record in ledger as confirmed (before placing)
            purchase_id = self._ledger.record_purchase_request(
                item_description=item_description,
                merchant="rotorvillage.ca",
                amount=order_total,
            )
            self._ledger.mark_confirmed(purchase_id)

            # Step 12: Place the order
            log.info("RV checkout: placing order...")
            ok, order_id, msg = await self._place_order()

            if ok:
                self._ledger.mark_completed(purchase_id, gift_card_id=gift_card["id"], notes=f"Order {order_id}")
                log.info(f"RV checkout: order placed successfully — {order_id}")
                return {"ok": True, "message": f"Order placed. {msg}", "order_id": order_id}
            else:
                self._ledger.mark_failed(purchase_id, notes=msg)
                return {"ok": False, "message": f"Order placement failed: {msg}", "order_id": None}

        except Exception as e:
            log.error(f"RV checkout: unexpected error: {e}", exc_info=True)
            return {"ok": False, "message": f"Unexpected error: {e}", "order_id": None}
        finally:
            if self._browser:
                try:
                    await self._browser.close()
                except Exception:
                    pass
            if self._playwright:
                try:
                    await self._playwright.stop()
                except Exception:
                    pass

    # ------------------------------------------------------------------
    # Step implementations
    # ------------------------------------------------------------------

    async def _login(self) -> tuple[bool, str]:
        await self._page.goto("https://rotorvillage.ca/login.php", wait_until="domcontentloaded")
        await self._page.wait_for_timeout(1500)
        try:
            await self._page.fill("input[name='login_email']", self._email)
            await self._page.fill("input[name='login_pass']", self._password)
            await self._page.locator("form[action*='login'] input[type='submit']").first.click()
            await self._page.wait_for_timeout(2500)
            if "login" in self._page.url.lower() and "order_status" not in self._page.url:
                return False, f"Still on login page: {self._page.url}"
            return True, "OK"
        except Exception as e:
            return False, str(e)

    async def _clear_cart(self):
        """Remove all items from cart before adding our product."""
        try:
            await self._page.goto("https://rotorvillage.ca/cart.php", wait_until="domcontentloaded")
            await self._page.wait_for_timeout(1000)
            # Click all "Remove" buttons
            while True:
                remove_btns = await self._page.locator(
                    "a[data-cart-itemid], .cart-remove button, button:has-text('Remove')"
                ).all()
                if not remove_btns:
                    break
                await remove_btns[0].click()
                await self._page.wait_for_timeout(1000)
        except Exception as e:
            log.debug(f"Cart clear: {e}")

    async def _add_to_cart(self, product_url: str) -> tuple[bool, str]:
        await self._page.goto(product_url, wait_until="domcontentloaded")
        await self._page.wait_for_timeout(1500)

        for sel in ["#form-action-addToCart", "button[data-button-type='add-cart']",
                    "button:has-text('Add to Cart')"]:
            try:
                btn = self._page.locator(sel).first
                if await btn.is_visible(timeout=2000):
                    await btn.click()
                    await self._page.wait_for_timeout(2000)
                    return True, "OK"
            except Exception:
                continue

        return False, "No add-to-cart button found — product may be out of stock."

    async def _proceed_to_checkout(self) -> tuple[bool, str]:
        await self._page.goto("https://rotorvillage.ca/cart.php", wait_until="domcontentloaded")
        await self._page.wait_for_timeout(1500)

        cart_items = await self._page.locator(".cart-item").count()
        if cart_items == 0:
            return False, "Cart is empty."

        for sel in ["a:has-text('Check Out')", "a:has-text('Proceed to Checkout')",
                    "a.cart-actions__checkout-button"]:
            try:
                btn = self._page.locator(sel).first
                if await btn.is_visible(timeout=2000):
                    await btn.click()
                    await self._page.wait_for_timeout(3000)
                    if "checkout" in self._page.url.lower():
                        return True, "OK"
            except Exception:
                continue

        return False, f"Could not reach checkout page. URL: {self._page.url}"

    async def _select_shipping(self) -> tuple[Optional[str], float]:
        """
        Select shipping according to the configured priority list.
        Returns (selected_label, cost_cad) or (None, 0) if nothing matched.
        """
        preferred = self._config.get(
            "purchasing.shipping.preferred_services",
            ["Canada Post Xpresspost", "Canada Post"]
        )
        fallback = self._config.get("purchasing.shipping.fallback", "cheapest")

        await self._page.wait_for_timeout(1500)

        # Collect all visible shipping options with their labels and costs
        shipping_inputs = await self._page.locator("input[name*='shippingOptionIds']").all()
        options = []
        for inp in shipping_inputs:
            try:
                inp_id = await inp.get_attribute("id") or ""
                label_text = ""

                # Try label[for='id'] first
                if inp_id:
                    try:
                        label_text = await self._page.locator(
                            f"label[for='{inp_id}']"
                        ).first.inner_text(timeout=500)
                    except Exception:
                        pass

                # Fallback: look for the nearest label via JS
                if not label_text:
                    try:
                        label_text = await inp.evaluate(
                            """el => {
                                const label = el.closest('label') ||
                                    document.querySelector('label[for="' + el.id + '"]') ||
                                    el.parentElement?.querySelector('label');
                                return label ? label.innerText : '';
                            }"""
                        )
                    except Exception:
                        pass

                # Fallback: check the parent container for visible text
                if not label_text:
                    try:
                        label_text = await inp.evaluate(
                            "el => el.parentElement?.parentElement?.innerText || ''"
                        )
                    except Exception:
                        pass

                cost_match = re.search(r'\$([0-9]+\.[0-9]+)', label_text)
                if cost_match:
                    cost = float(cost_match.group(1))
                else:
                    cost = 0.0
                options.append({"input": inp, "label": label_text, "cost": cost, "id": inp_id})
                log.info(f"Shipping option found: {label_text.strip()!r} -> ${cost:.2f}")
            except Exception:
                continue

        if not options:
            return None, 0.0

        # Try preferred services in priority order — match must be specific
        # enough to avoid false positives (e.g. "xpress" matching "UPS Express")
        PREFERRED_PATTERNS = {
            "Canada Post Xpresspost": ["xpresspost", "canada post (xpresspost)", "cp xpresspost"],
            "Canada Post":            ["canada post"],
        }

        for preferred_name in preferred:
            patterns = PREFERRED_PATTERNS.get(preferred_name, [preferred_name.lower()])
            for opt in options:
                label_lower = opt["label"].lower()
                if any(p in label_lower for p in patterns):
                    log.info(f"Selecting shipping: {opt['label'].strip()!r} (${opt['cost']:.2f})")
                    opt_id = opt["id"] or ""
                    if opt_id:
                        try:
                            await self._page.locator(f"label[for='{opt_id}']").first.click()
                        except Exception:
                            await self._page.evaluate(f"document.getElementById('{opt_id}').click()")
                    await self._page.wait_for_timeout(1000)
                    await self._click_shipping_continue()
                    return opt["label"].strip(), opt["cost"]

        # Fallback — pick cheapest non-pickup, non-combine option
        # Never pick the most expensive option just because it contains "xpress"
        non_pickup = [
            o for o in options
            if "local" not in o["label"].lower()
            and "pickup" not in o["label"].lower()
            and "combine" not in o["label"].lower()
            and o["cost"] > 0
        ]
        if fallback == "cheapest" and non_pickup:
            cheapest = min(non_pickup, key=lambda o: o["cost"])
            log.info(f"Fallback cheapest: {cheapest['label'].strip()!r} (${cheapest['cost']:.2f})")
            cheapest_id = cheapest["id"] or ""
            if cheapest_id:
                try:
                    await self._page.locator(f"label[for='{cheapest_id}']").first.click()
                except Exception:
                    await self._page.evaluate(f"document.getElementById('{cheapest_id}').click()")
            await self._page.wait_for_timeout(1000)
            await self._click_shipping_continue()
            return cheapest["label"].strip(), cheapest["cost"]
        elif fallback == "fastest" and non_pickup:
            fastest = non_pickup[0]  # first option is typically fastest
            fastest_id = fastest["id"] or ""
            if fastest_id:
                try:
                    await self._page.locator(f"label[for='{fastest_id}']").first.click()
                except Exception:
                    await self._page.evaluate(f"document.getElementById('{fastest_id}').click()")
            await self._page.wait_for_timeout(1000)
            await self._click_shipping_continue()
            return fastest["label"].strip(), fastest["cost"]

        return None, 0.0

    async def _click_shipping_continue(self):
        """Click the Continue button after shipping selection to advance to payment."""
        continue_sels = [
            ".checkout-step--shipping .checkout-step__continue button",
            "button[data-test='step-payment-button']",
            ".checkout-step--shipping button[type='button']",
            "button:has-text('Continue')",
        ]
        for sel in continue_sels:
            try:
                btn = self._page.locator(sel).first
                if await btn.is_visible(timeout=2000):
                    await btn.click()
                    await self._page.wait_for_timeout(3000)
                    log.info(f"Shipping continue clicked: {sel}")
                    return
            except Exception:
                continue
        log.debug("No shipping continue button found — may have advanced automatically")

    async def _apply_visa_mc(self, card_data: str) -> tuple[bool, str]:
        """
        Enter a Visa/MC prepaid card via PayPal's hosted card fields.
        RotorVillage uses PayPal's zoid-based iframe card fields — each field
        (number, expiry, name, CVV) is a separate iframe loading from paypal.com.
        Standard page.fill() won't reach inside them; we use frame_locator()
        to enter each iframe's context individually.

        card_data format: "card_number|MM/YY|CVV|Name On Card"
        e.g. "4111111111111111|12/27|123|William Kew"
        Name is optional — falls back to empty string if not provided.
        """
        try:
            parts = card_data.split("|")
            if len(parts) < 3:
                return False, "Visa/MC card data must be 'number|MM/YY|CVV' or 'number|MM/YY|CVV|Name' format."
            card_number = parts[0].strip()
            expiry      = parts[1].strip()
            cvv         = parts[2].strip()
            name        = parts[3].strip() if len(parts) > 3 else ""

            # Make sure Credit Card radio is selected first
            try:
                cc_radio = self._page.locator("#radio-paypalcommercecreditcards").first
                if await cc_radio.is_visible(timeout=2000):
                    await cc_radio.click()
                    await self._page.wait_for_timeout(1500)
            except Exception:
                pass

            # PayPal zoid iframes — each is a separate iframe identified by
            # the 'name' attribute containing the field type keyword.
            # We enter each iframe using frame_locator and fill the single
            # input inside it.
            iframe_fields = [
                ("card_number_field", card_number, "card number"),
                ("card_expiry_field", expiry,       "expiry"),
                ("card_name_field",   name,          "name") if name else None,
                ("card_cvv_field",    cvv,           "CVV"),
            ]

            for field_info in iframe_fields:
                if field_info is None:
                    continue
                field_name_part, value, label = field_info
                try:
                    # The iframe name contains the field type, e.g.
                    # __zoid__paypal_card_number_field__...
                    frame = self._page.frame_locator(f"iframe[name*='{field_name_part}']").first
                    inp   = frame.locator("input").first
                    await inp.wait_for(timeout=5000)
                    await inp.click()
                    await self._page.wait_for_timeout(300)
                    await inp.fill(value)
                    await self._page.wait_for_timeout(400)
                    log.info(f"PayPal card field '{label}' filled")
                except Exception as e:
                    log.warning(f"PayPal card field '{label}': {e}")
                    # Non-fatal for name field — some merchants don't show it

            log.info("PayPal card fields filled")
            return True, "OK"
        except Exception as e:
            return False, str(e)

    async def _apply_gift_card(self, code: str) -> tuple[bool, str]:
        """Click the gift certificate link, enter the code, apply it."""
        try:
            # Click the "gift" link to reveal the input
            gift_link = self._page.locator("a:has-text('gift')").first
            if await gift_link.is_visible(timeout=2000):
                await gift_link.click()
                await self._page.wait_for_timeout(1000)

            gc_input_sels = [
                "input[name='giftcertcode']",
                "input[placeholder*='gift' i]",
                "input[name*='gift' i]",
            ]
            for sel in gc_input_sels:
                try:
                    inp = self._page.locator(sel).first
                    if await inp.is_visible(timeout=1500):
                        await inp.fill(code)
                        await self._page.wait_for_timeout(500)
                        # Click Apply button
                        for apply_sel in ["button:has-text('Apply')", "input[value*='Apply' i]"]:
                            try:
                                apply_btn = self._page.locator(apply_sel).first
                                if await apply_btn.is_visible(timeout=1000):
                                    await apply_btn.click()
                                    await self._page.wait_for_timeout(1500)
                                    log.info(f"Gift card code applied")
                                    return True, "OK"
                            except Exception:
                                continue
                except Exception:
                    continue

            return False, "Could not find gift certificate input field"
        except Exception as e:
            return False, str(e)

    async def _read_order_total(self) -> tuple[float, float]:
        """Read the order total and item subtotal from the checkout summary."""
        order_total = 0.0
        item_total  = 0.0
        try:
            # BigCommerce checkout order summary selectors
            total_sels = [
                ".cart-priceItem--total .cart-priceItem-value",
                "[data-test='cart-total'] .cart-priceItem-value",
                ".optimizedCheckout-orderSummary-cartSection:last-child .cart-priceItem-value",
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
                ".cart-priceItem--subtotal .cart-priceItem-value",
                "[data-test='cart-subtotal'] .cart-priceItem-value",
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
            log.warning(f"Could not read order total: {e}")

        return order_total, item_total

    async def _place_order(self) -> tuple[bool, str, str]:
        """Click place order and extract the confirmation order number."""
        place_sels = [
            "button:has-text('Place Order')",
            "button:has-text('Submit Order')",
            "button[type='submit']:has-text('Order')",
            ".checkout-button--primary",
        ]
        for sel in place_sels:
            try:
                btn = self._page.locator(sel).first
                if await btn.is_visible(timeout=3000):
                    await btn.click()
                    log.info("Clicked place order button")
                    await self._page.wait_for_timeout(5000)

                    # Look for order confirmation
                    confirm_sels = [
                        ".orderConfirmation-title",
                        "h1:has-text('Thank')",
                        "h1:has-text('Order')",
                        "[data-test='order-confirmation-heading']",
                    ]
                    for csel in confirm_sels:
                        try:
                            if await self._page.locator(csel).count() > 0:
                                heading = await self._page.locator(csel).first.inner_text()
                                # Try to extract order number from URL or page
                                order_id = ""
                                url_match = re.search(r'orderId=(\d+)', self._page.url)
                                if url_match:
                                    order_id = url_match.group(1)
                                else:
                                    page_text = await self._page.inner_text("body")
                                    num_match = re.search(r'order\s*#?\s*(\d{5,})', page_text, re.IGNORECASE)
                                    if num_match:
                                        order_id = num_match.group(1)
                                return True, order_id, f"Order confirmed: {heading}"
                        except Exception:
                            continue

                    # If we got here, check if URL changed away from checkout
                    if "checkout" not in self._page.url.lower():
                        return True, "", f"Order likely placed — now at: {self._page.url}"

                    return False, "", "Clicked place order but no confirmation detected"
            except Exception:
                continue

        return False, "", "Could not find place order button"


# ------------------------------------------------------------------
# Sync wrapper for the tool registry (which calls run() synchronously)
# ------------------------------------------------------------------

def execute_purchase_sync(product_url: str, item_description: str,
                          confirm_callback_sync, config, ledger) -> dict:
    """
    Synchronous entry point for the execute_purchase tool.
    confirm_callback_sync: callable(summary: str) -> bool  (blocking)
    """
    async def async_confirm(summary: str) -> bool:
        return confirm_callback_sync(summary)

    checkout = RotorVillageCheckout(config=config, ledger=ledger)

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

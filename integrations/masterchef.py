"""
MasterChef Mode
===============
Gordon Ramsay-voiced cooking companion. Manages menus, shopping lists,
recipes, and step-by-step guidance. Pure in-memory data + session state --
no external API or network dependency at all (unlike the watchalong sports
integrations), so nothing here can fail from a bad connection.
"""

from dataclasses import dataclass, field
from typing import Optional

# ── Cuisine definitions ───────────────────────────────────

CUISINES = {
    "mexican": {
        "name": "Mexican Street Food",
        "emoji": "\U0001F32E",
        "vibe": "Bold, fresh, smoky. The kind of food you'd find at a taco stand at 11pm.",
        "pantry": ["cumin", "smoked paprika", "dried chilli flakes", "garlic", "lime", "coriander", "oregano"],
        "pre_made": ["corn tortillas", "flour tortillas", "tinned black beans", "tinned tomatoes",
                     "soured cream", "pickled jalapeños"],
        "hero_dishes": ["Carne Asada Tacos", "Chicken Tinga Quesadillas", "Elote (Mexican Street Corn)",
                        "Guacamole and Pico de Gallo", "Enchiladas Verdes", "Chorizo and Potato Tacos"],
        "gordon_note": "Mexican food is about freshness and balance. If your salsa doesn't taste bright "
                       "and alive, it's dead before it hits the table.",
    },
    "italian": {
        "name": "Italian",
        "emoji": "\U0001F35D",
        "vibe": "Simple ingredients, executed with absolute precision.",
        "pantry": ["good olive oil", "garlic", "dried chilli", "tinned San Marzano tomatoes", "parmesan",
                   "fresh basil", "sea salt", "black pepper"],
        "pre_made": ["De Cecco dried pasta", "good pizza dough", "tinned San Marzano tomatoes",
                     "good quality stock"],
        "hero_dishes": ["Cacio e Pepe", "Aglio e Olio", "Spaghetti Bolognese", "Margherita Pizza",
                        "Arancini", "Chicken Piccata", "Pasta al Pomodoro"],
        "gordon_note": "Italian cooking is not complicated. It is NOT. The ingredients do the work. "
                       "Your job is to not ruin them. Which, right now, is not guaranteed.",
    },
    "chinese": {
        "name": "Chinese Takeout",
        "emoji": "\U0001F961",
        "vibe": "High heat, balance of sweet-savoury-umami, textures that contrast.",
        "pantry": ["soy sauce", "oyster sauce", "sesame oil", "Shaoxing rice wine", "ginger", "garlic",
                   "cornstarch", "white pepper"],
        "pre_made": ["good jasmine rice", "pre-made wonton wrappers", "dumpling wrappers", "dried noodles",
                     "tinned water chestnuts", "tinned bamboo shoots"],
        "hero_dishes": ["Kung Pao Chicken", "Egg Fried Rice", "Beef and Broccoli",
                        "Pan-Fried Dumplings (Potstickers)", "Mapo Tofu", "Sweet and Sour Pork", "Spring Rolls"],
        "gordon_note": "The wok. That wok needs to be SCREAMING hot. If you're not getting smoke, "
                       "you're getting steamed mush. That is not a stir fry. That is a disaster.",
    },
    "thai": {
        "name": "Thai Street Food",
        "emoji": "\U0001F35C",
        "vibe": "Sweet, sour, salty, spicy. All four. At once. Every bite.",
        "pantry": ["fish sauce", "palm sugar", "lime", "Thai red curry paste", "coconut milk",
                   "dried chillies", "lemongrass", "galangal", "kaffir lime leaves"],
        "pre_made": ["flat rice noodles", "rice vermicelli", "tinned coconut milk", "good fish sauce",
                     "Thai red/green curry paste", "tamarind concentrate"],
        "hero_dishes": ["Pad Thai", "Green Curry", "Red Curry", "Thai Basil Chicken (Pad Krapow)",
                        "Tom Yum Soup", "Mango Sticky Rice", "Spring Rolls with Peanut Sauce"],
        "gordon_note": "Thai food lives and dies by balance. Taste it. Adjust it. More fish sauce, "
                       "more lime, more sugar -- you're building a profile, not following a formula.",
    },
    "desserts_baking": {
        "name": "Single-Serve Baking",
        "emoji": "\U0001F950",
        "vibe": "Precision. Baking is chemistry. Get it wrong and you get a biscuit.",
        "pantry": ["unsalted butter", "caster sugar", "plain flour", "baking powder", "vanilla extract",
                   "sea salt flakes", "good chocolate", "eggs", "brown sugar"],
        "pre_made": ["good quality chocolate chips", "good vanilla extract",
                     "all-butter ready-roll puff pastry", "good cocoa powder"],
        "hero_dishes": ["Perfect Chocolate Chip Cookies", "Butter Croissants (cheat method)",
                        "Fudgy Brownies", "Shortbread", "Chocolate Lava Cakes", "Madeleines"],
        "gordon_note": "Baking is the one place where I cannot save you if you improvise. Follow the "
                       "method. Weigh everything. And for the love of God, use ROOM TEMPERATURE butter.",
    },
}


# ── Recipes database ──────────────────────────────────────
# Each recipe: name, cuisine, serves, time, difficulty, gordon_intro,
# shopping (proteins/produce/pre_made/pantry), steps (id/title/time_min/
# gordon/technique/youtube/warning), gordon_finish.

RECIPES = {
    "carne_asada_tacos": {
        "name": "Carne Asada Tacos", "cuisine": "mexican", "serves": 4,
        "time": "30 minutes + 30 min marinade", "difficulty": "medium",
        "gordon_intro": "Right. Carne asada. This is about the marinade and -- crucially -- the resting. "
                        "If you cut into this meat straight off the heat, I will personally come to your kitchen.",
        "shopping": {
            "proteins": ["500g flank steak or skirt steak"],
            "produce": ["4 limes", "1 orange", "6 garlic cloves", "1 bunch coriander", "2 white onions",
                        "2 jalapeños", "4 ripe avocados", "4 tomatoes"],
            "pre_made": ["12 corn tortillas (good quality)", "soured cream"],
            "pantry": ["cumin", "smoked paprika", "dried oregano", "olive oil", "salt", "black pepper",
                       "pickled jalapeños"],
        },
        "steps": [
            {"id": 1, "title": "The Marinade", "time_min": 5,
             "gordon": "The marinade. Juice of two limes, half an orange, three crushed garlic cloves, a "
                       "teaspoon of cumin, smoked paprika, dried oregano, olive oil, salt and pepper. Mix "
                       "it. Taste it. Should be bright, punchy, alive. Yes?",
             "technique": None,
             "warning": "Acid-heavy marinade -- don't leave the steak in longer than 2 hours or it starts "
                        "to cure the meat. Not what we want."},
            {"id": 2, "title": "Marinade the Steak", "time_min": 30,
             "gordon": "Score the steak lightly on both sides -- cross hatch, not deep -- and pour the "
                       "marinade over. Work it in with your hands. 30 minutes minimum. Not negotiable.",
             "technique": None, "warning": None},
            {"id": 3, "title": "Make the Guacamole", "time_min": 10,
             "gordon": "While the steak marinates. Two ripe avocados -- they should give when you press "
                       "them, not collapse. Halve them, stone out, scoop into a bowl. Lime juice, salt, "
                       "diced onion, diced tomato, chopped coriander, half a jalapeño finely diced. Mash it "
                       "coarsely -- NOT smooth. Guacamole is not baby food.",
             "technique": "avocado_pit_removal", "youtube": "avocado pit removal technique",
             "warning": "Taste and season. Every. Single. Time."},
            {"id": 4, "title": "Make Pico de Gallo", "time_min": 8,
             "gordon": "Two tomatoes. Dice them properly -- not chunks, not mush. 5mm dice. Half a white "
                       "onion, same size. One jalapeño, seeds out if you're sensitive. Lime juice, salt, "
                       "coriander. Set aside. Let it sit.",
             "technique": "knife_dicing", "youtube": "knife dicing technique vegetables", "warning": None},
            {"id": 5, "title": "Cook the Steak", "time_min": 8,
             "gordon": "Pan or grill -- screaming hot. Cast iron if you have it. Season the steak one more "
                       "time with salt and pepper. 2-3 minutes each side for medium. DO NOT TOUCH IT while "
                       "it's cooking. Leave it alone. You're not a DJ. Stop flipping.",
             "technique": "searing_steak", "youtube": "how to sear steak properly cooking technique",
             "warning": "Smoke is good. Smoke means heat. No smoke = no colour = no flavour."},
            {"id": 6, "title": "REST THE STEAK", "time_min": 5,
             "gordon": "REST. IT. Off the heat, onto a board. 5 minutes. I am not joking. If you cut into "
                       "it now I will -- just. Let it rest.",
             "technique": None,
             "warning": "This is not optional. The juices redistribute. Cut it now and they run out. Dry "
                        "steak. Your fault."},
            {"id": 7, "title": "Slice and Serve", "time_min": 5,
             "gordon": "AGAINST the grain. Look at the fibres in the meat -- cut perpendicular to them. "
                       "Thin slices. Warm your tortillas in a dry pan for 30 seconds each side. Steak, "
                       "guac, pico, soured cream, pickled jalapeños. Squeeze of lime. Done.",
             "technique": "slicing_against_grain", "youtube": "how to slice steak against the grain",
             "warning": None},
        ],
        "gordon_finish": "If that tasted good -- and it should, because the recipe is solid -- it's "
                        "because you finally listened. Don't get comfortable.",
    },

    "pad_thai": {
        "name": "Pad Thai", "cuisine": "thai", "serves": 2, "time": "20 minutes", "difficulty": "medium",
        "gordon_intro": "Pad Thai. Everybody orders it, nobody makes it properly at home. The secret? "
                        "High heat and the sauce made BEFORE you start cooking. If you're making the "
                        "sauce while the noodles are in the wok, you've already failed.",
        "shopping": {
            "proteins": ["300g raw king prawns or chicken breast", "2 eggs"],
            "produce": ["4 spring onions", "2 garlic cloves", "200g beansprouts", "1 lime",
                        "small bunch coriander"],
            "pre_made": ["200g flat rice noodles (dried)", "fish sauce", "tamarind concentrate",
                         "palm sugar or brown sugar", "dried chilli flakes", "roasted peanuts (unsalted)"],
            "pantry": ["vegetable oil", "salt"],
        },
        "steps": [
            {"id": 1, "title": "Soak the Noodles", "time_min": 20,
             "gordon": "Flat rice noodles into cold water. Not boiling, not warm -- cold. 20 minutes. "
                       "They'll soften. They'll finish in the wok.",
             "technique": None,
             "warning": "Over-soaked noodles will disintegrate in the wok. They should still have bite "
                        "when they go in."},
            {"id": 2, "title": "Make the Pad Thai Sauce", "time_min": 3,
             "gordon": "The sauce. This is non-negotiable -- do this FIRST. 3 tablespoons fish sauce, 2 "
                       "tablespoons tamarind concentrate, 1 tablespoon palm sugar. Mix. Taste. Should be -- "
                       "simultaneously -- sour, salty, and sweet. Adjust accordingly. Remember: you can "
                       "add, you cannot subtract.",
             "technique": None, "youtube": "pad thai sauce recipe balance fish sauce tamarind",
             "warning": "Every fish sauce brand is different. Taste before you use it."},
            {"id": 3, "title": "Prep Everything", "time_min": 10,
             "gordon": "Mise en place. Everything ready before the wok goes on. Prawns peeled and "
                       "deveined. Garlic minced. Spring onions sliced -- whites and greens separated. "
                       "Eggs cracked into a bowl. Peanuts roughly chopped. Go.",
             "technique": "deveining_prawns", "youtube": "how to devein prawns quickly",
             "warning": "Once the wok is hot there is no going back to prep. Everything must be ready."},
            {"id": 4, "title": "The Wok", "time_min": 8,
             "gordon": "Wok. High heat. SCREAMING hot. Add oil, swirl. Garlic in -- 30 seconds. Prawns "
                       "in -- 2 minutes until pink. Push everything to the side. Eggs in the centre, "
                       "scramble them in the wok. Don't let them fully set before you combine. Noodles in, "
                       "sauce in. Toss. Toss properly. Everything moves.",
             "technique": "wok_technique", "youtube": "wok tossing technique stir fry",
             "warning": "If your wok isn't smoking you're steaming not stir frying."},
            {"id": 5, "title": "Finish and Plate", "time_min": 2,
             "gordon": "Off the heat. Beansprouts in -- they need 30 seconds, no more. Plate it. Peanuts "
                       "on top. Spring onion greens. Coriander. Lime wedge on the side. Dried chilli if "
                       "you want heat. That's it. Taste it. Does it taste like the restaurant? It should.",
             "technique": None, "warning": None},
        ],
        "gordon_finish": "See? That's Pad Thai. Properly made. Not that glue you get delivered in a box.",
    },

    "chocolate_chip_cookies": {
        "name": "Perfect Chocolate Chip Cookies", "cuisine": "desserts_baking", "serves": 24,
        "time": "45 minutes + 30 min chill", "difficulty": "easy",
        "gordon_intro": "Chocolate chip cookies. Simple. Except everyone gets them wrong. Too cakey, too "
                        "thin, wrong chocolate, wrong butter temperature. We're doing this once, properly, "
                        "and you'll never use another recipe.",
        "shopping": {
            "pantry": ["225g unsalted butter (ROOM TEMPERATURE)", "200g caster sugar",
                       "165g dark brown sugar (packed)", "2 large eggs (room temperature)",
                       "2 tsp vanilla extract", "340g plain flour", "1 tsp bicarbonate of soda",
                       "1.5 tsp fine sea salt", "350g dark chocolate chips or chopped chocolate",
                       "flaky sea salt for finishing"],
        },
        "steps": [
            {"id": 1, "title": "Butter Temperature CHECK", "time_min": 0,
             "gordon": "Stop. Before you do ANYTHING. Touch your butter. It should give when you press it "
                       "but NOT be greasy or shiny. That is room temperature butter. Cold butter = cake. "
                       "Melted butter = flat disaster. If yours is wrong, step away from the bowl.",
             "technique": "butter_temperature", "youtube": "how to soften butter quickly room temperature",
             "warning": "This is the most important step and it's before you've done anything."},
            {"id": 2, "title": "Cream Butter and Sugars", "time_min": 5,
             "gordon": "Butter into the bowl. Both sugars in. Beat them together -- properly. 3-4 minutes "
                       "in a stand mixer or by hand. It should go pale, almost white, and fluffy. If it's "
                       "still yellow and dense, keep going. You're building air. That air is your texture.",
             "technique": "creaming_method", "youtube": "creaming butter sugar properly baking technique",
             "warning": "Under-creaming is the most common mistake. Don't rush this."},
            {"id": 3, "title": "Add Eggs and Vanilla", "time_min": 3,
             "gordon": "Eggs in one at a time. Not together. ONE AT A TIME. Beat after each addition until "
                       "incorporated. Vanilla in. The mixture should look smooth and slightly glossy. If "
                       "it's split and curdled your eggs were too cold. Absolute schoolboy error.",
             "technique": None,
             "warning": "Cold eggs cause the mixture to split. Room temperature eggs. Always."},
            {"id": 4, "title": "Fold in Dry Ingredients", "time_min": 3,
             "gordon": "Flour, bicarb, and salt sifted together. Now -- FOLD it in. Slowly. You are not "
                       "trying to wake the gluten up. Mix until JUST combined. Streaks of flour are fine "
                       "at this stage. Stop before you think you should.",
             "technique": "folding_technique", "youtube": "folding technique baking flour cookies",
             "warning": "Overmixing = tough cookies. They'll look like hockey pucks."},
            {"id": 5, "title": "Add Chocolate and Chill", "time_min": 30,
             "gordon": "Chocolate chips in. Fold gently. Now -- cover the bowl and into the fridge for 30 "
                       "minutes. Minimum. An hour is better. Overnight is best. The cold firms the fat "
                       "which slows the spread. Thick cookies with crispy edges and a fudgy centre. THAT "
                       "is what we're after.",
             "technique": None, "warning": "Skip the chill and you get flat, greasy discs. Not cookies."},
            {"id": 6, "title": "Bake", "time_min": 11,
             "gordon": "Oven to 190°C fan. Line your trays. Scoop the dough -- 2 tablespoons per cookie, "
                       "roll into a ball. Space them 5cm apart -- they spread. Sea salt flakes on top. "
                       "Into the oven. 10-12 minutes. They should look UNDERDONE when you take them out. "
                       "Golden at the edges, pale in the centre. Trust the process.",
             "technique": None, "youtube": "how to know when cookies are done baking",
             "warning": "Overbaked cookies are dry and disappointing. They continue cooking on the tray "
                        "out of the oven."},
            {"id": 7, "title": "Cool on the Tray", "time_min": 10,
             "gordon": "Leave them. ON the tray. 10 minutes. They're too soft to move and they're still "
                       "cooking. This is where that fudgy centre sets. If you move them now -- if you so "
                       "much as LOOK at them wrong -- they'll fall apart. You've waited 30 minutes for the "
                       "dough. You can wait 10 minutes now.",
             "technique": None, "warning": None},
        ],
        "gordon_finish": "If those are gooey in the centre with crispy edges -- beautiful. That's a "
                        "proper chocolate chip cookie. Not that pre-made dough nonsense. Well done. Don't "
                        "mess with the recipe.",
    },

    "cacio_e_pepe": {
        "name": "Cacio e Pepe", "cuisine": "italian", "serves": 2, "time": "20 minutes", "difficulty": "hard",
        "gordon_intro": "Cacio e Pepe. Three ingredients. Pasta, pecorino, black pepper. And it will "
                        "HUMBLE you. This dish has destroyed professional chefs. Get the technique wrong "
                        "and you get scrambled eggs with pasta. Get it right and you'll understand why "
                        "Italian food is the greatest cuisine on earth.",
        "shopping": {
            "pre_made": ["200g dried spaghetti or tonnarelli (De Cecco, not supermarket own brand)",
                         "100g Pecorino Romano (freshly grated)", "50g Parmesan (freshly grated)",
                         "black peppercorns (whole, to grind fresh)"],
            "pantry": ["coarse sea salt"],
        },
        "steps": [
            {"id": 1, "title": "Pasta Water -- SERIOUSLY salty", "time_min": 10,
             "gordon": "Large pot. Lots of water. Bring to a boil. Salt it until it tastes like the sea. "
                       "Not like seawater -- like the SEA. This is not negotiable. Unsalted pasta water "
                       "makes unsalted pasta. The pasta will not absorb enough salt from the sauce. "
                       "Season the water.",
             "technique": None,
             "warning": "Most people use 10x too little salt in their pasta water. If you're worried, add "
                        "more."},
            {"id": 2, "title": "Toast the Pepper", "time_min": 3,
             "gordon": "Whole black peppercorns into a dry pan. Medium heat. Toast until fragrant -- 1-2 "
                       "minutes. Then CRUSH them. Not fine powder, not whole -- cracked. Coarse. Uneven. "
                       "That texture is the point. Pre-ground pepper from a jar is an insult.",
             "technique": "crushing_peppercorns", "youtube": "how to crack black pepper mortar pestle",
             "warning": "Whole peppercorns have no flavour until they're cracked. The oils are locked "
                        "inside."},
            {"id": 3, "title": "Grate the Cheese", "time_min": 5,
             "gordon": "Pecorino and Parmesan -- freshly grated. Not the stuff in a green tube. Not "
                       "pre-grated bags. FRESHLY grated. Fine grate, like snow. Mix them together in a "
                       "bowl. Set aside.",
             "technique": None,
             "warning": "Pre-grated cheese has anti-caking agents that prevent it from melting properly. "
                        "You'll get clumps. It'll break. It'll look terrible."},
            {"id": 4, "title": "Cook the Pasta", "time_min": 9,
             "gordon": "Pasta into the boiling water. Cook it 2 minutes LESS than the packet says -- it "
                       "finishes in the sauce. Save at least 200ml of the pasta water before you drain. "
                       "That starchy water is the sauce. Don't lose it.",
             "technique": None,
             "warning": "If you forget to save the pasta water this dish is over. Not a metaphor. It is "
                        "literally not possible to make Cacio e Pepe without it."},
            {"id": 5, "title": "The Sauce -- DO NOT rush this", "time_min": 5,
             "gordon": "Empty pan, medium-low heat. Toasted pepper in. Add a ladleful of pasta water, let "
                       "it bubble. Pasta into the pan, toss to coat. OFF THE HEAT. This is critical. Add "
                       "cheese in small amounts, tossing constantly, adding small splashes of pasta water "
                       "to emulsify. It should look creamy, glossy, and coat every strand. If it looks "
                       "clumpy -- more water, more tossing, lower heat.",
             "technique": "pasta_emulsification", "youtube": "cacio e pepe technique pasta water emulsification",
             "warning": "Too much heat and the cheese seizes. SEIZED cheese cannot be saved. Low heat. "
                        "Patience. The pan coming off the heat is not a mistake, it is the technique."},
        ],
        "gordon_finish": "If that is creamy and glossy -- and if you followed the method, it is -- you've "
                        "just made one of the most technically demanding pasta dishes in the Italian "
                        "canon. With three ingredients. Don't ruin it by adding cream next time.",
    },

    "kung_pao_chicken": {
        "name": "Kung Pao Chicken", "cuisine": "chinese", "serves": 3, "time": "25 minutes", "difficulty": "medium",
        "gordon_intro": "Kung Pao Chicken. Sweet, savoury, numbing heat from the Sichuan peppercorns, and "
                        "peanuts for crunch. Get the velveting right on the chicken and this is restaurant "
                        "quality. Skip it and it's rubber.",
        "shopping": {
            "proteins": ["400g chicken thigh, boneless skinless, diced"],
            "produce": ["4 dried red chillies", "1 red pepper", "3 spring onions", "3 garlic cloves",
                        "thumb of ginger"],
            "pre_made": ["roasted unsalted peanuts", "soy sauce", "dark soy sauce", "Shaoxing rice wine",
                         "rice vinegar", "sesame oil"],
            "pantry": ["cornstarch", "sugar", "vegetable oil", "Sichuan peppercorns (if you have them)"],
        },
        "steps": [
            {"id": 1, "title": "Velvet the Chicken", "time_min": 10,
             "gordon": "Diced chicken thigh. Toss with a tablespoon of Shaoxing wine, a teaspoon of soy "
                       "sauce, and a tablespoon of cornstarch. Mix until every piece is coated. This is "
                       "velveting -- it's what keeps the chicken tender through high heat instead of "
                       "turning to rubber. 10 minutes minimum, sitting.",
             "technique": "velveting_chicken", "youtube": "velveting chicken technique chinese cooking",
             "warning": "Skip this and the chicken goes tough the second it hits the wok."},
            {"id": 2, "title": "Make the Sauce", "time_min": 3,
             "gordon": "Two tablespoons soy sauce, one dark soy, one rice vinegar, one sugar, one "
                       "cornstarch, three tablespoons water. Mix it in a small bowl until the cornstarch "
                       "is fully dissolved, no lumps. This goes in at the end -- have it ready now.",
             "technique": None, "warning": "Lumpy cornstarch slurry means a lumpy sauce. Whisk it properly."},
            {"id": 3, "title": "The Wok -- Chillies and Aromatics", "time_min": 3,
             "gordon": "Wok screaming hot. Oil in. Dried chillies and Sichuan peppercorns first if you "
                       "have them -- 20 seconds, until fragrant, not burnt. Garlic and ginger in, another "
                       "20 seconds.",
             "technique": "wok_technique", "youtube": "wok tossing technique stir fry",
             "warning": "Burnt chillies are bitter, not spicy. Watch them, don't walk away."},
            {"id": 4, "title": "Cook the Chicken", "time_min": 4,
             "gordon": "Chicken in, spread it out, let it sit for 30 seconds before tossing -- you want "
                       "colour, not steam. Cook through, about 3-4 minutes. Red pepper in for the last "
                       "minute, just to soften slightly, still with bite.",
             "technique": None, "warning": "Don't overcrowd the wok or it steams instead of sears."},
            {"id": 5, "title": "Sauce and Finish", "time_min": 3,
             "gordon": "Sauce in, toss to coat -- it'll thicken in seconds. Peanuts and spring onion "
                       "greens in, one more toss. Off the heat. Taste it. Sweet, savoury, a little heat. "
                       "Balanced.",
             "technique": None, "warning": None},
        ],
        "gordon_finish": "That's Kung Pao done properly -- tender chicken, glossy sauce, peanuts still "
                        "crunchy. Not the beige, gloopy version you get delivered.",
    },

    "green_curry": {
        "name": "Green Curry", "cuisine": "thai", "serves": 4, "time": "30 minutes", "difficulty": "medium",
        "gordon_intro": "Green curry. The paste does the heavy lifting, but how you build the curry -- "
                        "frying the paste properly, splitting the coconut cream -- that's where it's won "
                        "or lost.",
        "shopping": {
            "proteins": ["500g chicken thigh, sliced"],
            "produce": ["1 aubergine or 200g green beans", "4 kaffir lime leaves", "small bunch Thai basil",
                        "2 red chillies (to garnish)"],
            "pre_made": ["good green curry paste", "2 tins full-fat coconut milk", "fish sauce",
                         "palm sugar"],
            "pantry": ["vegetable oil"],
        },
        "steps": [
            {"id": 1, "title": "Split the Coconut Cream", "time_min": 5,
             "gordon": "Open the tins WITHOUT shaking them. Scoop just the thick cream off the top into a "
                       "dry wok or pan, medium heat, no oil. Let it bubble and split -- you'll see the oil "
                       "separate out. That's what we want, that's where the flavour builds.",
             "technique": None,
             "warning": "Shake the tin and the cream and water mix together -- you'll never get it to "
                        "split properly."},
            {"id": 2, "title": "Fry the Paste", "time_min": 5,
             "gordon": "Curry paste into the split coconut cream. Fry it, stirring constantly, 3-4 "
                       "minutes, until the oil goes red and it smells like it could knock you over. That "
                       "smell is the paste actually cooking, not just heating up.",
             "technique": None,
             "warning": "Undercooked paste tastes raw and one-dimensional. Give it the time."},
            {"id": 3, "title": "Chicken and Liquid", "time_min": 8,
             "gordon": "Chicken in, coat it in the paste, 2 minutes. Rest of the coconut milk in, kaffir "
                       "lime leaves torn in half and dropped in -- tearing releases the oil in the leaf. "
                       "Simmer, don't boil, 8 minutes.",
             "technique": None, "warning": "A hard boil will split the sauce and toughen the chicken."},
            {"id": 4, "title": "Vegetables and Seasoning", "time_min": 8,
             "gordon": "Aubergine or beans in, simmer until just tender. Fish sauce and palm sugar to "
                       "taste -- start with a tablespoon of each, adjust. Should be salty, a little sweet, "
                       "fragrant. Taste it properly before you decide it's done.",
             "technique": None, "warning": "Don't dump all the seasoning in at once -- build it, tasting each time."},
            {"id": 5, "title": "Finish", "time_min": 2,
             "gordon": "Off the heat. Thai basil torn in at the last second -- it wilts fast and turns "
                       "bitter if it cooks. Sliced red chilli on top. Serve over jasmine rice.",
             "technique": None, "warning": None},
        ],
        "gordon_finish": "That's a proper green curry -- the paste actually cooked, the coconut cream "
                        "split and built back up, balanced heat. Miles from anything out of a jar alone.",
    },

    "croissants_cheat_method": {
        "name": "Butter Croissants (Cheat Method)", "cuisine": "desserts_baking", "serves": 8,
        "time": "35 minutes", "difficulty": "easy",
        "gordon_intro": "I am NOT asking you to laminate dough on a Tuesday. Get all-butter puff pastry --"
                        " Careme, not supermarket own brand -- and we'll shape and bake it properly. The "
                        "technique is still the technique.",
        "shopping": {
            "pre_made": ["2 sheets all-butter puff pastry (Careme or similar, not supermarket own brand)"],
            "pantry": ["1 egg (for egg wash)", "flaky sea salt or caster sugar (optional finish)"],
        },
        "steps": [
            {"id": 1, "title": "Temperature Check the Pastry", "time_min": 5,
             "gordon": "Pastry needs to be cold but pliable -- straight from the fridge, not the freezer. "
                       "If it cracks when you unroll it, it's too cold, give it a couple of minutes. If "
                       "it's sticky and soft, it's too warm -- back in the fridge.",
             "technique": None,
             "warning": "Warm pastry won't hold its shape or layers when it bakes. Butter needs to stay solid until the oven."},
            {"id": 2, "title": "Cut the Triangles", "time_min": 8,
             "gordon": "Unroll onto a lightly floured surface. Cut into long triangles, base about 10cm, "
                       "tip elongated -- like a very thin isoceles triangle. Clean cuts, sharp knife, "
                       "straight down, don't drag it.",
             "technique": "knife_dicing", "youtube": "how to cut croissant dough triangles",
             "warning": "A dragged cut seals the pastry layers together at the edge and they won't puff properly."},
            {"id": 3, "title": "Roll the Croissants", "time_min": 10,
             "gordon": "Small cut in the centre of the base -- half a centimetre, no more. Roll from the "
                       "base to the tip, snug but not tight, tip ending underneath. Curve the ends in "
                       "slightly for the classic shape. Onto a lined tray, spaced well apart.",
             "technique": None, "warning": "Roll them too tight and they can't expand in the oven."},
            {"id": 4, "title": "Egg Wash and Proof", "time_min": 20,
             "gordon": "Beaten egg, brushed thinly over each one -- avoid the cut edges, egg wash there "
                       "glues the layers shut. Rest them at room temperature 15-20 minutes before baking. "
                       "This isn't optional, it relaxes the gluten.",
             "technique": None, "warning": None},
            {"id": 5, "title": "Bake", "time_min": 18,
             "gordon": "200°C fan, no fan if you can help the shape holding, 16-18 minutes, until deep "
                       "golden brown -- not pale gold, DEEP golden. That colour is the sugars caramelising "
                       "and it's where the flavour is. Second egg wash halfway through if you want extra shine.",
             "technique": None, "youtube": "how to know when croissants are baked done",
             "warning": "Pull them too early and the centre stays raw and doughy underneath a nice colour."},
        ],
        "gordon_finish": "Flaky, golden, buttery layers -- and you didn't spend six hours laminating "
                        "dough. That's the smart use of a shortcut. The technique that's left is still real.",
    },
}


@dataclass
class MasterChefSession:
    """Active cooking session state. In-memory only, process lifetime --
    starting a new session replaces the old one, nothing persists to disk."""
    cuisine: str
    menu: list = field(default_factory=list)       # list of dish keys (RECIPES keys)
    current_dish: str = ""                          # RECIPES key
    current_step: int = 0                           # index into current recipe's steps
    shopping_list: dict = field(default_factory=dict)
    notes: list = field(default_factory=list)
    started_at: float = 0.0
    active: bool = False


# Singleton session
_session: Optional[MasterChefSession] = None


def get_session() -> Optional[MasterChefSession]:
    return _session


def set_session(s: MasterChefSession):
    global _session
    _session = s


def clear_session():
    global _session
    _session = None


_STOPWORDS = {"the", "a", "an", "my", "some", "that", "this", "recipe", "dish"}


def find_recipe_key(dish_name: str) -> Optional[str]:
    """Fuzzy-ish match against RECIPES, roughly matching natural speech
    ("the tacos", "let's do cacio e pepe") rather than requiring an exact
    key or display name. Tries, in order: exact key, exact display name,
    whole-phrase substring, then a token-overlap match ignoring filler
    words like "the"/"my". Returns the RECIPES key or None."""
    if not dish_name:
        return None
    key_guess = dish_name.strip().lower().replace(" ", "_").replace("-", "_")
    if key_guess in RECIPES:
        return key_guess

    name_lower = dish_name.strip().lower()
    for key, recipe in RECIPES.items():
        if recipe["name"].lower() == name_lower:
            return key
    for key, recipe in RECIPES.items():
        if name_lower in recipe["name"].lower() or name_lower in key:
            return key

    tokens = {t for t in name_lower.replace("-", " ").split() if t not in _STOPWORDS and len(t) > 2}
    if not tokens:
        return None
    best_key, best_overlap = None, 0
    for key, recipe in RECIPES.items():
        haystack = {t for t in (recipe["name"].lower() + " " + key.replace("_", " ")).split() if t not in _STOPWORDS}
        overlap = len(tokens & haystack)
        if overlap > best_overlap:
            best_overlap, best_key = overlap, key
    return best_key

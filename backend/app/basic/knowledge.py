"""The built-in knowledge base. Short, stable facts only; every entry has keywords used for matching.

Edit freely: add {"k": [...keywords...], "a": "answer"} entries. Matching is keyword-based (no AI)."""

ARIA_HELP = [
    {"k": ["connect", "model", "api key", "key", "setup", "set up", "ai model", "start chatting"],
     "a": "To get full AI answers, connect a model on the chat screen: click **Download Ollama** for free AI that runs "
          "on your PC (no key), or paste an Anthropic (`sk-ant-…`) or OpenAI (`sk-…`) API key in the **Connect an AI "
          "model** box. Until then I answer basic questions from my built-in knowledge."},
    {"k": ["ollama", "free", "local", "offline", "without key", "no key"],
     "a": "**Free local AI:** install Ollama from https://ollama.com/download, then open the chat screen and click "
          "**Download and use** to get a model (about 2 GB once). Aria connects to it automatically; your chats stay "
          "on your computer. It needs about 8 GB of RAM."},
    {"k": ["what can you do", "features", "capabilities", "help", "how to use", "aria"],
     "a": "I'm **Aria**. With an AI model connected I can chat, research the web with sources, write and run code, "
          "analyse your documents and spreadsheets, run teams of agents and automate workflows. Right now, without "
          "a model, I can answer basic questions: simple facts, math (`12*7+3`), unit conversions (`5 km in miles`), "
          "the date and time, and search your uploaded documents."},
    {"k": ["project", "projects"],
     "a": "**Projects** group chats, documents, code workspaces and research with shared instructions. Open "
          "**Projects** in the menu, create one, then start a chat inside it."},
    {"k": ["document", "upload", "pdf", "file", "files"],
     "a": "Upload files on the **Documents** page (PDF, Word, Excel, PowerPoint, text, images). Even in basic mode I "
          "can search them by keywords; with a model connected I can summarise, compare and answer questions with "
          "citations."},
    {"k": ["skill", "skills"], "a": "**Skills** are reusable instructions (e.g. Summarize, Translate). Find them in "
          "the menu under **Skills**, or use the ✦ button in the chat box. They need an AI model."},
    {"k": ["automation", "automations", "schedule", "workflow"],
     "a": "**Automations** run multi-step AI workflows on demand, on a schedule or from a webhook. Open "
          "**Automations** in the menu. They need an AI model."},
    {"k": ["memory", "remember", "forget"],
     "a": "Aria can remember things you approve (Memory in the profile menu). You can view, edit, export or delete "
          "every memory at any time."},
    {"k": ["web search", "internet", "search the web", "google", "browse"],
     "a": "Web search needs a free Tavily key (https://tavily.com): paste it on the chat screen after connecting a "
          "model. I can't browse the web in basic mode."},
    {"k": ["privacy", "private", "data", "secure", "security"],
     "a": "Aria runs on your own computer or server. Your chats, files and keys are stored in its local database. "
          "With free local AI (Ollama), nothing leaves your PC."},
]

SMALL_TALK = [
    {"k": ["how are you", "how are you doing", "how's it going"],
     "a": "I'm doing well, thanks for asking! I'm in basic mode right now, so ask me simple facts, math or unit conversions."},
    {"k": ["joke", "tell me a joke", "funny"],
     "a": "Why do programmers prefer dark mode? Because light attracts bugs. 🐛"},
    {"k": ["good night", "bye", "goodbye", "see you"], "a": "Goodbye! Come back any time. 👋"},
]

GENERAL = [
    {"k": ["planets", "solar system", "how many planets"],
     "a": "The Solar System has **8 planets**, from the Sun outwards: Mercury, Venus, Earth, Mars, Jupiter, Saturn, "
          "Uranus and Neptune. Pluto is classified as a dwarf planet (since 2006)."},
    {"k": ["largest planet", "biggest planet", "jupiter"], "a": "**Jupiter** is the largest planet in the Solar System."},
    {"k": ["smallest planet", "mercury planet"], "a": "**Mercury** is the smallest planet and the closest to the Sun."},
    {"k": ["distance", "sun", "earth from the sun", "astronomical unit"],
     "a": "Earth is on average about **150 million km** (93 million miles) from the Sun — 1 astronomical unit."},
    {"k": ["moon", "distance to the moon"], "a": "The Moon is on average about **384,400 km** (238,900 miles) from Earth."},
    {"k": ["speed of light", "light speed"], "a": "The speed of light in a vacuum is **299,792,458 m/s** (about 300,000 km/s)."},
    {"k": ["speed of sound"], "a": "Sound travels at about **343 m/s** in dry air at 20 °C."},
    {"k": ["gravity", "acceleration due to gravity", "9.8"],
     "a": "Gravity at Earth's surface accelerates objects at about **9.81 m/s²**. Gravity is the attraction between "
          "masses, described by Newton's law and, more precisely, by Einstein's general relativity."},
    {"k": ["boiling point", "water boil", "boils"],
     "a": "Pure water boils at **100 °C (212 °F)** at sea-level pressure; it boils at lower temperatures at altitude."},
    {"k": ["freezing point", "water freeze", "freezes"], "a": "Pure water freezes at **0 °C (32 °F)** at normal pressure."},
    {"k": ["water formula", "h2o", "chemical formula of water"], "a": "Water is **H₂O**: two hydrogen atoms and one oxygen atom."},
    {"k": ["photosynthesis"],
     "a": "**Photosynthesis** is how plants, algae and some bacteria use sunlight to turn carbon dioxide and water "
          "into glucose (sugar) and oxygen: 6CO₂ + 6H₂O + light → C₆H₁₂O₆ + 6O₂."},
    {"k": ["dna"], "a": "**DNA** (deoxyribonucleic acid) is the molecule that carries genetic instructions in living "
          "things. It is a double helix made of four bases: adenine, thymine, guanine and cytosine."},
    {"k": ["atom", "atoms"], "a": "An **atom** is the smallest unit of a chemical element: a nucleus of protons and "
          "neutrons surrounded by electrons."},
    {"k": ["cell", "cells", "basic unit of life"], "a": "The **cell** is the basic unit of life. Humans have tens of trillions of cells."},
    {"k": ["periodic table", "elements", "how many elements"],
     "a": "The periodic table has **118** confirmed chemical elements, arranged by atomic number. Hydrogen (1) is the lightest."},
    {"k": ["oxygen", "air", "atmosphere", "composition of air"],
     "a": "Earth's air is about **78% nitrogen, 21% oxygen**, 0.9% argon and small amounts of carbon dioxide and other gases."},
    {"k": ["continents", "how many continents"],
     "a": "There are **7 continents** (common English model): Asia, Africa, North America, South America, Antarctica, "
          "Europe and Australia/Oceania. Asia is the largest."},
    {"k": ["oceans", "largest ocean", "pacific"],
     "a": "The five oceans are the Pacific, Atlantic, Indian, Southern and Arctic. The **Pacific** is the largest and deepest."},
    {"k": ["highest mountain", "tallest mountain", "everest"],
     "a": "**Mount Everest** (Nepal/China) is the highest mountain above sea level, about **8,849 m** (29,032 ft)."},
    {"k": ["longest river", "nile", "amazon"],
     "a": "The **Nile** (about 6,650 km) and the **Amazon** (about 6,400 km) are the two longest rivers; which is "
          "longer depends on how the source is measured. The Amazon carries by far the most water."},
    {"k": ["largest country", "biggest country", "russia area"], "a": "By area, **Russia** is the largest country (about 17.1 million km²)."},
    {"k": ["largest desert", "biggest desert", "sahara"],
     "a": "The largest desert is **Antarctica** (a cold desert). The largest hot desert is the **Sahara** in North Africa."},
    {"k": ["human body", "bones", "how many bones"], "a": "An adult human skeleton has **206 bones**."},
    {"k": ["heart", "heartbeat", "resting heart rate"],
     "a": "The heart pumps blood around the body. A normal adult resting heart rate is about **60–100 beats per "
          "minute**. (General information, not medical advice.)"},
    {"k": ["blood types", "blood group"], "a": "The main blood groups are **A, B, AB and O**, each Rh-positive or Rh-negative."},
    {"k": ["vitamin c", "vitamins"], "a": "Vitamins are nutrients the body needs in small amounts. Vitamin C (found "
          "in citrus fruit, peppers, broccoli) supports the immune system and skin. (General information, not medical advice.)"},
    {"k": ["water per day", "drink water", "hydration"],
     "a": "Needs vary with body size, activity and climate; a common guide is roughly **2–3 litres of fluids a day** "
          "for adults, including water in food and drinks. (General information, not medical advice.)"},
    {"k": ["ai", "artificial intelligence", "what is ai"],
     "a": "**Artificial intelligence (AI)** is software that performs tasks that normally need human intelligence, "
          "such as understanding language, recognising images or making decisions. Large language models (LLMs) "
          "like Claude or GPT are AI models trained on large amounts of text."},
    {"k": ["machine learning", "ml"], "a": "**Machine learning** is a branch of AI where computers learn patterns from data instead of being explicitly programmed."},
    {"k": ["internet", "how does the internet work"],
     "a": "The **internet** is a global network of computers that exchange data using shared protocols (TCP/IP). The "
          "World Wide Web is one service on it: web pages linked together and viewed in a browser."},
    {"k": ["cpu", "processor"], "a": "The **CPU** (central processing unit) is the chip that runs a computer's instructions."},
    {"k": ["ram", "memory computer"], "a": "**RAM** is a computer's fast, temporary working memory; it is cleared when the computer turns off."},
    {"k": ["gpu", "graphics card"], "a": "A **GPU** (graphics processing unit) does many calculations in parallel; it draws graphics and speeds up AI."},
    {"k": ["python"], "a": "**Python** is a popular, easy-to-read programming language used for web apps, data analysis, automation and AI."},
    {"k": ["javascript"], "a": "**JavaScript** is the programming language of web pages; it also runs on servers with Node.js."},
    {"k": ["html"], "a": "**HTML** (HyperText Markup Language) describes the structure of web pages; CSS styles them and JavaScript makes them interactive."},
    {"k": ["binary"], "a": "**Binary** is the base-2 number system (only 0 and 1) that computers use internally. Example: decimal 5 = binary 101."},
    {"k": ["password", "strong password"],
     "a": "A strong password is long (12+ characters), unique for each site, and ideally a random passphrase stored in "
          "a password manager. Turn on two-factor authentication where possible."},
    {"k": ["world war 2", "ww2", "second world war"], "a": "**World War II** lasted from **1939 to 1945**."},
    {"k": ["world war 1", "ww1", "first world war"], "a": "**World War I** lasted from **1914 to 1918**."},
    {"k": ["moon landing", "first man on the moon", "armstrong"],
     "a": "**Neil Armstrong** was the first person to walk on the Moon, on **20 July 1969** (Apollo 11)."},
    {"k": ["independence day india", "india independence"], "a": "India became independent on **15 August 1947**. Republic Day is 26 January (constitution in force since 1950)."},
    {"k": ["us independence", "american independence", "july 4"], "a": "The United States declared independence on **4 July 1776**."},
    {"k": ["days in a year", "leap year"],
     "a": "A common year has **365 days**; a leap year has 366 (29 February). Leap years are divisible by 4, except "
          "century years not divisible by 400 (2000 was a leap year, 1900 was not)."},
    {"k": ["week", "days in a week", "months", "months in a year"], "a": "There are **7 days** in a week and **12 months** in a year."},
    {"k": ["pi", "value of pi"], "a": "**π (pi)** ≈ **3.14159**: the ratio of a circle's circumference to its diameter."},
    {"k": ["area of a circle", "circle area"], "a": "Area of a circle = **π × r²** (r = radius). Circumference = 2πr."},
    {"k": ["pythagoras", "pythagorean"], "a": "**Pythagoras' theorem:** in a right triangle, a² + b² = c², where c is the hypotenuse."},
    {"k": ["prime number", "primes"], "a": "A **prime number** has exactly two divisors: 1 and itself. The first primes are 2, 3, 5, 7, 11, 13, 17, 19, 23, 29."},
]

CAPITALS = {
    "afghanistan": "Kabul", "argentina": "Buenos Aires", "australia": "Canberra", "austria": "Vienna",
    "bangladesh": "Dhaka", "belgium": "Brussels", "brazil": "Brasília", "canada": "Ottawa", "chile": "Santiago",
    "china": "Beijing", "colombia": "Bogotá", "denmark": "Copenhagen", "egypt": "Cairo", "ethiopia": "Addis Ababa",
    "finland": "Helsinki", "france": "Paris", "germany": "Berlin", "ghana": "Accra", "greece": "Athens",
    "india": "New Delhi", "indonesia": "Jakarta", "iran": "Tehran", "iraq": "Baghdad", "ireland": "Dublin",
    "italy": "Rome", "japan": "Tokyo", "kenya": "Nairobi", "malaysia": "Kuala Lumpur", "mexico": "Mexico City",
    "nepal": "Kathmandu", "netherlands": "Amsterdam (the government sits in The Hague)", "new zealand": "Wellington",
    "nigeria": "Abuja", "norway": "Oslo", "pakistan": "Islamabad", "peru": "Lima", "philippines": "Manila",
    "poland": "Warsaw", "portugal": "Lisbon", "qatar": "Doha", "russia": "Moscow", "saudi arabia": "Riyadh",
    "singapore": "Singapore", "south africa": "Pretoria (executive), Cape Town (legislative) and Bloemfontein (judicial)",
    "south korea": "Seoul", "spain": "Madrid", "sri lanka": "Sri Jayawardenepura Kotte (official); Colombo is the largest city",
    "sweden": "Stockholm", "switzerland": "Bern", "thailand": "Bangkok", "turkey": "Ankara",
    "united arab emirates": "Abu Dhabi", "uae": "Abu Dhabi", "united kingdom": "London", "uk": "London",
    "england": "London", "united states": "Washington, D.C.", "usa": "Washington, D.C.", "us": "Washington, D.C.",
    "america": "Washington, D.C.", "vietnam": "Hanoi", "ukraine": "Kyiv", "israel": None,
}

ENTRIES = ARIA_HELP + SMALL_TALK + GENERAL

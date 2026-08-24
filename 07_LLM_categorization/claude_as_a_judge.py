"""
Claude-as-a-Judge: classify environmental actions on synagogue web pages.

Uses claude-haiku-4-5 via the Anthropic API. The full 76-label task
description is in the system prompt with cache_control, so it is only
paid at full price once per 5-min cache TTL.

Requires:
  ANTHROPIC_API_KEY set in ../.env
"""

import os
from pathlib import Path

import anthropic
from dotenv import load_dotenv

# ── load credentials ──────────────────────────────────────────────────────────

load_dotenv(Path(__file__).parent.parent / ".env")
client = anthropic.Anthropic(api_key=os.getenv("ANTHROPIC_API_KEY"))

# ── system prompt — fully static, eligible for caching ───────────────────────
# The entire task description lives here so every call after the first
# pays cache-read rates (~10x cheaper) for these ~1,200 tokens.

SYSTEM_PROMPT = """You are a text classifier.

#Context: The data comes from US synagogue websites. Your task is to classify the environmental action discussed on the web page provided by the user.

# Task: Output the name of exactly one classification label from the list below. No explanation is needed. If the page does not contain an actual environmental action, return "N/A".

# Valid labels (return exactly one):
"Spirituality & Worship_Include symbols of nature in place of worship, gardens, or meditation areas"
"NA_Focus on environemntalism (stewardship, climate action, etc.) in worship for religious or community events (holidays, earth day, etc.)"
"NA_Incorporate environmental prayers/songs in worship"
"NA_Dedicate a worship service to environmentalism"
"NA_Offer retreats, prayers, or reflections to reconnect with nature"
"NA_Use environemtnally-friendly alternatives for items used in worship"
"Community_Promote and facilitate household energy audits and water conservation"
"NA_Assisted in local environmental cleanup, restoration projects or tree planting initiatives"
"NA_Encouraged car-pooling, public transit use, and/or bicycle use"
"NA_Encourage volunteering initiatives aimed at environmental responsibility, reducing individual carbon footprints, and taking climate action"
"NA_Resources with a nature and environmental stewardship focus are available in the faith communitys library, common area, or website"
"NA_Encourage members to adopt a greener lifestyle, to join community environmental projects, and to practice the 3 R's at home"
"NA_Planted a community garden for native pollinator-friendly plants and/or vegetables"
"NA_Bike racks and carpooling preferential parking spots have been installed on the faith community's property"
"NA_Hold or support a holiday or summer camp with an environmental stewardship/climate theme"
"NA_Hold educational sessions to educate people on environmental stewardship/climate action that link to the faith tradition and promote faith-based and local environmental organizations"
"NA_Undertake environmental projects in collaboration with other faith communities or local groups (e.g. planting pollinator gardens, planting trees, community spring clean-up, naturalization of schoolyards and sacred spaces etc.)"
"NA_Engage with local, provincial, or federal governments to support sustainable public policies"
"NA_A volunteer coordinator has been appointed to facilitate carpooling and to ensure all community members are aware of public transit routes to the facility"
"NA_Electric vehicle charging station(s) have been installed on the property or identified nearby"
"NA_Create and support opportunities for younger people to experience how people in their own and other communities are affected by the planetary crisis and how they can work for change"
"NA_A board committee focusing on environmental and sustainability issues has been established"
"Operations & Maintenance_Have a 'think twice before printing' policy and an active paper reduction and recycling policy"
"NA_Have a procurement policy for recycled and/or FSC certified paper (e.g. office, envelopes, bulletins, bathroom tissue, etc.)"
"NA_A sustainability coordinator and/or team has been created and are developing sustainable practices within the place of worship"
"NA_Biodegradable, non-toxic, phosphate-free cleaning products are purchased and used within the facility"
"NA_Make their own eco-friendly cleaning products using natural materials (e.g. baking soda, white vinegar, essential oils, etc.)"
"NA_Have a green maintenance plan including using alternatives to salt in the winter and/or seeking lot and yard maintenance providers who use green practices"
"Energy_Utility expenditures and energy consumption are tracked and documented"
"NA_Utility data is analyzed regularly to identify activities that cause high consumption. Patterns are identified and plans are in place to reduce consumption"
"NA_An energy audit has been completed by a trained professional auditor and they are working to resolve some of the identified issues"
"NA_Signage posted at all light switches reminds people to turn off lights when not in use"
"NA_Energy efficient lighting (e.g. LEDs) have been installed where possible. Dimmers, timers, and motion sensors are installed where possible"
"NA_Programmable thermostats are in use and winter/summer set-back temperatures are in place"
"NA_Heating/cooling system has a regular maintenance schedule, furnace filters are cleaned or replaced, and ducts are clean, where applicable"
"NA_High-efficiency heating/cooling or solar hot water system installed"
"NA_Ceiling fans are in regular use"
"NA_Usage of caulk and spray foam to air-seal the building"
"NA_Weather stripping on windows and doors has been upgraded"
"NA_Replaced their fossil fuel burning heating system with an air or ground source heat pump"
"NA_Insulation has been added to walls and roof"
"NA_Investigated the possibility of installing a renewable energy system of any kind in their facility"
"NA_Installed a renewable energy system in their facility"
"Water_Basic efficiency steps have been taken such as low-flow aerators on all faucets, posting signs advising people to limit water usage, and regular checks to ensure no leaky faucets or toilets"
"NA_Systems that limit total hot water usage such as a hot water heat recovery system or a pre-mixing of hot and cold water system have been installed"
"NA_Steps have been taken such as insulating hot water tanks and pipes"
"NA_Installed a high-efficiency hot water heater, such as an on-demand unit or a solar domestic hot water heating system"
"NA_Low-flush toilets and urinals are installed where possible throughout the facility"
"NA_A grey water system has been installed that allows the facility to reuse water for a second time or a large cistern has been installed to capture rainwater for interior building uses"
"NA_Exterior watering of the grounds only occurs in the morning or evening and is shut off when it is raining or rain is expected"
"NA_The grounds have been specifically landscaped with native pollinator plants that do not require much watering (xeriscaping) or installed a rain garden to capture runoff"
"NA_Rain barrels have been installed to collect roof run-off water for the purpose of watering the grounds"
"Waste_Blue bins can be found in every area. Signs are posted near the bins advising people as to what can and can't be recycled"
"NA_Recycling facilities are available at the faith community for dropping off items such as ink cartridges, cell phones, CFL bulbs, etc"
"NA_Items such as eye glasses and clothes are donated to the faith community for re-use or re-distribution"
"NA_Have a 'backyard' composting system for yard waste and kitchen waste"
"NA_Composts regularly through their municipal composting program"
"NA_Reusable or cloth bags are used while shopping at grocery stores to reduce plastic waste"
"NA_Fruits and vegetables are selected from a bin, rather than those on styrofoam trays and shrink-wrapped in plastic"
"NA_A waste reduction strategy has been planned and implemented. Track total waste to determine if targets are being met"
"Kitchen_Locally sourced food and drinks are used when possible"
"NA_Vegetarian, vegan, organic, and in-season food options are provided and served when possible"
"NA_Reusable, recyclable, and/or compostable dishes and cutlery are in regular use"
"NA_A policy of using washable dishes and cutlery, etc. during events and break times is in place"
"NA_Single-use dishes and cutlery have been completely eliminated from use"
"NA_Promotes fair trade through a purchasing policy and by offering fairly traded products for sale"
"NA_Community garden regularly provides fresh vegetables to events held at the facility or to local food programs"
"NA_Support local food initiatives through actions such as hosting a local farmers market or other types of food-related events"
"Environmental & Climate Justice_Studied its faith traditions recent statements on fossil fuel divestment"
"NA_Formed an environmental justice discussion group and climate justice themes have been incorporated into services and events and promoted across social media platforms"
"NA_Met with political leader(s) to urge them to honour the rights of Indigenous peoples to a clean and healthy environment and to take immediate action on behalf of Indigenous communities impacted by environmental pollution (land, water, air)"
"NA_Researched the traditional territory and treaty on which it is located and incorporates a statement of land acknowledgement into its services and events"
"NA_Hosted a workshop or discussion group concerning environmental issues being faced by Indigenous communities today, and committed to taking action"
"NA_Members have been provided with educational materials about fossil fuel divestment"
"NA_Hosts environmental/climate justice events (screenings, workshops, study sessions) and/or offers space for community groups to meet on these issues (online and in person)"
"NA_Reviewed their investments and agreed to divest from any fossil fuel investments OR to invest in renewable energy"
"NA_Met with political leader(s) to call for action on climate change and participating in a climate justice action"
"NA_Other"
"N/A"
"""

# ── prompt builder — user message contains only the page content ──────────────

def build_prompt(website_content: str) -> str:
    return (
        f'Page content:\n"""{website_content}"""\n\n'
        "Return exactly one label from the valid labels listed in the instructions."
    )


# ── inference ─────────────────────────────────────────────────────────────────

def get_response_judge(text: str) -> str:
    """Classify the environmental action in *text* using claude-haiku-4-5.

    The system prompt (76-label task description) is cached after the first
    call, so subsequent calls only pay cache-read rates for the static part.
    """
    response = client.messages.create(
        model="claude-haiku-4-5",
        max_tokens=50,
        system=[
            {
                "type": "text",
                "text": SYSTEM_PROMPT,
                "cache_control": {"type": "ephemeral"},
            }
        ],
        messages=[
            {"role": "user", "content": build_prompt(text)},
        ],
    )
    return response.content[0].text.strip()

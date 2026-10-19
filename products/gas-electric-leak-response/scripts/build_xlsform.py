"""Build survey123/gas_leak_form.xlsx (XLSForm) whose question names match config/layer_schema.yaml.

Open the result in Survey123 Connect, review it, and publish. Change names here AND in layer_schema.yaml together.
"""
from pathlib import Path

from openpyxl import Workbook

OUT = Path(__file__).resolve().parents[1] / "survey123" / "gas_leak_form.xlsx"
survey = [
    ["type", "name", "label", "required", "readonly", "constraint", "constraint_message", "calculation", "appearance", "hint"],
    ["text", "Leak_ID", "Leak ID", "yes", "yes", "", "", "once(concat('LK-', format-date-time(now(), '%Y%m%d'), '-', substr(uuid(), 1, 8)))", "", "Auto-generated unique incident ID"],
    ["text", "Crew_Badge", "Technician badge number", "yes", "", "", "", "", "", ""],
    ["geopoint", "location", "Leak location", "yes", "", "", "", "", "", "Stand at the leak; accuracy better than 5 m is recommended"],
    ["decimal", "Gas_LEL_Pct", "Peak reading (% LEL)", "yes", "", ". >= 0 and . <= 100", "Enter 0-100 % LEL", "", "", "Combustible gas indicator"],
    ["integer", "Gas_PPM", "Peak reading (ppm)", "no", "", ". >= 0 and . <= 1000000", "Enter 0-1,000,000 ppm", "", "", "Leave blank if the instrument does not report ppm"],
    ["decimal", "Instrument_Temp_C", "Ambient temperature at instrument (C)", "no", "", ". >= -40 and . <= 60", "Outside the sensor range (-40 to 60 C)", "", "", ""],
    ["select_one pipeline_type", "Pipeline_Type", "Pipeline material", "yes", "", "", "", "", "minimal", ""],
    ["select_one pressure_class", "Pressure_Class", "Pressure class", "yes", "", "", "", "", "minimal", ""],
    ["select_one surface_cover", "Surface_Cover", "Surface cover over the leak", "yes", "", "", "", "", "minimal", "Sealed surfaces make gas migrate sideways"],
    ["image", "Photo_1", "Photo of leak site", "no", "", "", "", "", "", ""],
    ["image", "Photo_2", "Photo of combustible gas indicator screen", "no", "", "", "", "", "", ""],
    ["image", "Photo_3", "Additional photo", "no", "", "", "", "", "", ""],
    ["text", "Notes", "Notes", "no", "", "", "", "", "multiline", ""],
]
choices = [
    ["list_name", "name", "label"],
    ["pipeline_type", "cast_iron", "Cast iron"], ["pipeline_type", "bare_steel", "Bare steel"], ["pipeline_type", "polyethylene", "Polyethylene (PE)"],
    ["pressure_class", "high_transmission", "High-pressure transmission"], ["pressure_class", "medium_distribution", "Medium-pressure distribution"],
    ["pressure_class", "low_distribution", "Low-pressure distribution"],
    ["surface_cover", "asphalt", "Asphalt"], ["surface_cover", "concrete", "Concrete"], ["surface_cover", "soil", "Soil"], ["surface_cover", "grass", "Grass"],
]
settings = [["form_title", "form_id", "version", "default_language"], ["Gas Leak Inspection", "gas_leak_inspection", "1", "English"]]
wb = Workbook()
for title, rows in (("survey", survey), ("choices", choices), ("settings", settings)):
    ws = wb.active if title == "survey" else wb.create_sheet()
    ws.title = title
    for r in rows:
        ws.append(r)
OUT.parent.mkdir(exist_ok=True)
wb.save(OUT)
print("wrote", OUT)

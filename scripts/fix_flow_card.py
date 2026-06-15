"""Fix f-strings that contain backslash-escaped quotes inside braces (illegal Python)."""
import os, re

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
target = os.path.join(ROOT, 'custom_components', 'powmr_inverter', '__init__.py')

with open(target, 'r', encoding='utf-8') as f:
    content = f.read()

fixes = [
    ('lines.append(f"        pv_total_power: {_e(\\"pv_power\\")}")', 'lines.append("        pv_total_power: " + _e("pv_power"))'),
    ('lines.append(f"        grid_active_power: {_e(\\"grid_power\\")}")', 'lines.append("        grid_active_power: " + _e("grid_power"))'),
    ('lines.append(f"        consump: {_e(\\"load_power\\")}")', 'lines.append("        consump: " + _e("load_power"))'),
    ('lines.append(f"        battery_soc: {_e(\\"battery_soc_corrected\\")}")', 'lines.append("        battery_soc: " + _e("battery_soc_corrected"))'),
    ('lines.append(f"        battery_soc: {_e(\\"battery_soc\\")}")', 'lines.append("        battery_soc: " + _e("battery_soc"))'),
    ('lines.append(f"        battery_power: {_e(\\"battery_power\\")}")', 'lines.append("        battery_power: " + _e("battery_power"))'),
    ('lines.append(f"        battery_voltage: {_e(\\"battery_voltage\\")}")', 'lines.append("        battery_voltage: " + _e("battery_voltage"))'),
    ('lines.append(f"        today_pv: {_e(\\"daily_energy\\")}")', 'lines.append("        today_pv: " + _e("daily_energy"))'),
    ('lines.append(f"            entity: {_e(\\"pv_power\\")}")', 'lines.append("            entity: " + _e("pv_power"))'),
    ('lines.append(f"            entity: {_e(\\"load_power\\")}")', 'lines.append("            entity: " + _e("load_power"))'),
    ('lines.append(f"            entity: {_e(\\"battery_power\\")}")', 'lines.append("            entity: " + _e("battery_power"))'),
    ('lines.append(f"            entity: {_e(\\"grid_power\\")}")', 'lines.append("            entity: " + _e("grid_power"))'),
]

fixed_count = 0
for old, new in fixes:
    if old in content:
        content = content.replace(old, new)
        fixed_count += 1
    else:
        print(f'NOT FOUND (may already be fixed): {old[:55]}...')

print(f'Fixed {fixed_count}/{len(fixes)} f-string issues')

with open(target, 'w', encoding='utf-8') as f:
    f.write(content)

# Verify syntax
import ast
try:
    ast.parse(content)
    print('SYNTAX OK')
except SyntaxError as e:
    print(f'SYNTAX ERROR: {e}')
    # Show the offending line
    lines = content.split('\n')
    if e.lineno and e.lineno <= len(lines):
        for i in range(max(0, e.lineno-3), min(len(lines), e.lineno+2)):
            marker = '>>>' if i == e.lineno - 1 else '   '
            print(f'{marker} {i+1}: {lines[i]}')

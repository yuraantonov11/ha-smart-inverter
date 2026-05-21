#!/usr/bin/env python3
"""Fix initialValue -> value for DropdownButtonFormField in settings_tab.dart"""
import sys

path = r'lib/screens/settings_tab.dart'

with open(path, 'rb') as f:
    data = f.read()

# Detect line endings
if b'\r\n' in data:
    newline = b'\r\n'
else:
    newline = b'\n'

print(f"Detected line endings: {'CRLF' if newline == b'\r\n' else 'LF'}")

old1 = b'DropdownButtonFormField<HemsOptimizationStrategy>(' + newline + b'                  initialValue: selectedStrategy,'
new1 = b'DropdownButtonFormField<HemsOptimizationStrategy>(' + newline + b'                  value: selectedStrategy,'

old2 = b'DropdownButtonFormField<String>(' + newline + b'                  initialValue: selectedPreset.id,'
new2 = b'DropdownButtonFormField<String>(' + newline + b'                  value: selectedPreset.id,'

count1 = data.count(old1)
count2 = data.count(old2)
print(f"Found pattern 1 (strategy initialValue): {count1} occurrences")
print(f"Found pattern 2 (preset initialValue): {count2} occurrences")

data = data.replace(old1, new1)
data = data.replace(old2, new2)

with open(path, 'wb') as f:
    f.write(data)

print("Done writing file.")


#!/bin/sh
# bump_version.sh — оновлює version в manifest.json + створює git tag
# Usage: ./bump_version.sh <new_version>
# Example: ./bump_version.sh 1.9.0

set -e

if [ -z "$1" ]; then
    echo "Usage: $0 <new_version>"
    echo "Example: $0 1.9.0"
    echo "Current version:"
    python3 -c "import json; print(' ', json.load(open('manifest.json'))['version'])"
    exit 1
fi

NEW_VERSION="$1"
MANIFEST="manifest.json"

# Backup
cp "$MANIFEST" "$MANIFEST.bak"

# Update version using Python (safe JSON edit)
python3 <<EOF
import json
with open("$MANIFEST") as f:
    data = json.load(f)
old = data['version']
data['version'] = "$NEW_VERSION"
with open("$MANIFEST", 'w') as f:
    json.dump(data, f, indent=2)
    f.write('\n')
print(f"Updated: {old} → $NEW_VERSION")
EOF

echo ""
echo "Diff:"
diff "$MANIFEST.bak" "$MANIFEST"
rm "$MANIFEST.bak"

echo ""
echo "Next steps:"
echo "  1. Review the change above"
echo "  2. git add manifest.json"
echo "  3. git commit -m 'chore: bump version to v$NEW_VERSION'"
echo "  4. git tag v$NEW_VERSION"
echo "  5. git push origin main --tags  (after testing!)"

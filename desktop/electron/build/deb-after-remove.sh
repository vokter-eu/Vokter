#!/bin/bash
# Vokter deb post-remove. Replaces electron-builder's default after-remove
# template, so the default action is replicated VERBATIM, then the AppArmor
# profile installed by deb-after-install.sh is unloaded and deleted.
#
# Runs as root at `dpkg -r` time. Order matters: unload from the kernel FIRST,
# then remove the file. Both steps are failure-tolerant.

# --- default: drop the /usr/bin symlink -------------------------------------
if type update-alternatives >/dev/null 2>&1; then
    update-alternatives --remove 'vokter-desktop' '/usr/bin/vokter-desktop'
else
    rm -f '/usr/bin/vokter-desktop'
fi

# --- added: unload then delete the AppArmor profile -------------------------
if [ -f /etc/apparmor.d/vokter ]; then
    if command -v apparmor_parser >/dev/null 2>&1; then
        apparmor_parser -R /etc/apparmor.d/vokter 2>/dev/null || true
    fi
    rm -f /etc/apparmor.d/vokter || true
fi

exit 0

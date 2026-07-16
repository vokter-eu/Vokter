#!/bin/bash
# Vokter deb post-install. Replaces electron-builder's default after-install
# template (a custom afterInstall fully overrides the default), so everything
# the default did is replicated here VERBATIM, then the AppArmor block is added.
#
# Runs as root at `dpkg -i` time. What it does, in order:
#   1. Symlink /usr/bin/vokter-desktop -> /opt/Vokter/vokter-desktop  (default)
#   2. chrome-sandbox permissions: 0755 where user namespaces work,
#      setuid 4755 ONLY as a fallback on kernels without userns        (default)
#   3. mime / desktop database refresh                                 (default)
#   4. Install + load an AppArmor profile granting this binary `userns`
#      so it launches under Ubuntu 24.04's unprivileged-userns
#      restriction WITHOUT a setuid binary (the Chrome/VS Code pattern) (added)

set -e

# --- (1) default: /usr/bin symlink via update-alternatives ------------------
if type update-alternatives 2>/dev/null >&1; then
    # Remove previous link if it doesn't use update-alternatives
    if [ -L '/usr/bin/vokter-desktop' -a -e '/usr/bin/vokter-desktop' -a "`readlink '/usr/bin/vokter-desktop'`" != '/etc/alternatives/vokter-desktop' ]; then
        rm -f '/usr/bin/vokter-desktop'
    fi
    update-alternatives --install '/usr/bin/vokter-desktop' 'vokter-desktop' '/opt/Vokter/vokter-desktop' 100 || ln -sf '/opt/Vokter/vokter-desktop' '/usr/bin/vokter-desktop'
else
    ln -sf '/opt/Vokter/vokter-desktop' '/usr/bin/vokter-desktop'
fi

# --- (2) default: chrome-sandbox — setuid ONLY as last-resort fallback -------
# Check if user namespaces are supported by the kernel and working with a quick test:
if ! { [[ -L /proc/self/ns/user ]] && unshare --user true; }; then
    # Use SUID chrome-sandbox only on systems without user namespaces:
    chmod 4755 '/opt/Vokter/chrome-sandbox' || true
else
    chmod 0755 '/opt/Vokter/chrome-sandbox' || true
fi

# --- (3) default: mime / desktop databases ----------------------------------
if hash update-mime-database 2>/dev/null; then
    update-mime-database /usr/share/mime || true
fi

if hash update-desktop-database 2>/dev/null; then
    update-desktop-database /usr/share/applications || true
fi

# --- (4) added: AppArmor profile granting `userns` --------------------------
# Modern Ubuntu (24.04+) enforces kernel.apparmor_restrict_unprivileged_userns=1:
# an unconfined binary may be denied the CLONE_NEWUSER the Chromium sandbox needs.
# The fix that Chrome/Chromium/VS Code ship is a named profile that runs the
# binary unconfined but explicitly grants `userns` — NO setuid required.
# This is additive: the setuid fallback above still covers kernels with no
# userns at all. Everything here is failure-tolerant: on older AppArmor
# (no abi/4.0) or systems without AppArmor the load is skipped, and that is
# harmless because those systems don't enforce the restriction in the first
# place — a failed load must never abort the package install.
if command -v apparmor_parser >/dev/null 2>&1 && [ -d /etc/apparmor.d ]; then
    cat > /etc/apparmor.d/vokter <<'PROFILE'
# Auto-installed by the Vokter .deb. Grants the Vokter binary permission to
# create user namespaces under kernel.apparmor_restrict_unprivileged_userns=1.
# The profile is otherwise unconfined — it exists only to name the binary and
# hand it `userns`, exactly like Ubuntu's shipped /etc/apparmor.d/chrome.
abi <abi/4.0>,
include <tunables/global>

profile vokter /opt/Vokter/vokter-desktop flags=(unconfined) {
  userns,

  # Site-specific additions and overrides. See local/README for details.
  include if exists <local/vokter>
}
PROFILE
    # Load it now. Tolerate every failure mode (old parser, apparmor disabled).
    apparmor_parser -r -T -W /etc/apparmor.d/vokter 2>/dev/null || true
fi

exit 0

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

    # --- (4a) bwrap AppArmor profile (if the system lacks one) -------------------
    # The contained-browser fs cage uses bwrap (from the `bubblewrap` dependency).
    # Under apparmor_restrict_unprivileged_userns=1, bwrap needs a profile that
    # grants `userns` or it cannot create the namespace (RTM_NEWADDR: Operation
    # not permitted) and the whole cage fails. Ubuntu ships bwrap-userns-restrict
    # on updated systems, but NOT on every image (e.g. some 24.04 cloud images) —
    # install the standard STACKED profile ONLY if absent (never clobber a
    # system-provided one). No abi pin, so it loads across apparmor versions.
    if [ ! -f /etc/apparmor.d/bwrap-userns-restrict ]; then
        cat > /etc/apparmor.d/bwrap-userns-restrict <<'BWRAPPROF'
# Auto-installed by the Vokter .deb only when the system ships no bwrap profile.
# bwrap gets userns + caps within the userns, but its children are stacked into
# unpriv_bwrap (no caps) so bwrap cannot be used to by-pass the userns restriction.
include <tunables/global>
profile bwrap /usr/bin/bwrap flags=(attach_disconnected,mediate_deleted) {
  allow capability,
  allow file rwlkm /{**,},
  allow network,
  allow unix,
  allow ptrace,
  allow signal,
  allow mqueue,
  allow io_uring,
  allow userns,
  allow mount,
  allow umount,
  allow pivot_root,
  allow dbus,
  allow pix /** -> &bwrap//&unpriv_bwrap,
  include if exists <local/bwrap-userns-restrict>
}
profile unpriv_bwrap flags=(attach_disconnected,mediate_deleted) {
  allow file rwlkm /{**,},
  allow network,
  allow unix,
  allow ptrace,
  allow signal,
  allow mqueue,
  allow io_uring,
  allow userns,
  allow mount,
  allow umount,
  allow pivot_root,
  allow dbus,
  allow pix /** -> &unpriv_bwrap,
  audit deny capability,
  include if exists <local/unpriv_bwrap>
}
BWRAPPROF
        apparmor_parser -r -T -W /etc/apparmor.d/bwrap-userns-restrict 2>/dev/null || true
    fi

    # --- (4b) Tier-3 interactive-browser cage profiles ---------------------------
    # The contained browser runs: pasta (OWNS the netns) -> nft -> bwrap (fs cage,
    # from the `bubblewrap` dependency, which ships its own profile) -> the bundled
    # chromium-headless-shell. Under apparmor_restrict_unprivileged_userns=1 the
    # helper and the browser each need a profile granting `userns`, else the cage
    # cannot be built (see TIER3_PHASE1_BROWSER_CONTAINMENT.md). No `abi` pin: these
    # must load across apparmor versions (a pinned abi that isn't present fails the
    # load, which would silently disable the cage). Attach by the installed paths.
    RES=/opt/Vokter/resources/desktop/runtime
    cat > /etc/apparmor.d/vokter-pasta <<PROFILE
# Auto-installed by the Vokter .deb. Grants the bundled pasta permission to create a
# user+net namespace and run the cage inside it (modeled on bwrap-userns-restrict).
include <tunables/global>
profile vokter-pasta $RES/pasta/pasta flags=(attach_disconnected,mediate_deleted) {
  allow capability,
  allow file rwlkmix /{**,},
  allow network,
  allow unix,
  allow ptrace,
  allow signal,
  allow userns,
  allow mount,
  allow umount,
  allow pivot_root,
  include if exists <local/vokter-pasta>
}
PROFILE
    cat > /etc/apparmor.d/vokter-browser <<PROFILE
# Auto-installed by the Vokter .deb. Grants the bundled headless browser the userns
# its renderer sandbox needs (the Chrome/Chromium pattern), nested inside the cage.
include <tunables/global>
profile vokter-browser $RES/browser/chrome-headless-shell flags=(unconfined) {
  userns,
  include if exists <local/vokter-browser>
}
PROFILE
    apparmor_parser -r -T -W /etc/apparmor.d/vokter-pasta 2>/dev/null || true
    apparmor_parser -r -T -W /etc/apparmor.d/vokter-browser 2>/dev/null || true
fi

exit 0

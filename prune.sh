#!/usr/bin/bash
while getopts 'd' opt; do
    case "$opt" in
        d) DRY_RUN=true ;;
        *) echo "Usage $0 [-d]" >&2; exit 1
    esac
done
shift "$((OPTIND-1))"

export GH_PAGER=
DAYS_OLD=2

packages=(
aurorae
bluedevil
breeze-gtk
buildroot
extra-cmake-modules
flatpak-kcm
grub2-breeze-theme
kactivitymanagerd
kde-cli-tools
kde-gtk-config
kdecoration
kdeplasma-addons
kf6
kf6-attica
kf6-baloo
kf6-bluez-qt
kf6-breeze-icons
kf6-frameworkintegration
kf6-karchive
kf6-kauth
kf6-kbookmarks
kf6-kcalendarcore
kf6-kcmutils
kf6-kcodecs
kf6-kcolorscheme
kf6-kcompletion
kf6-kconfig
kf6-kconfigwidgets
kf6-kcontacts
kf6-kcoreaddons
kf6-kcrash
kf6-kdav
kf6-kdbusaddons
kf6-kdeclarative
kf6-kded
kf6-kdesu
kf6-kdnssd
kf6-kdoctools
kf6-kfilemetadata
kf6-kglobalaccel
kf6-kguiaddons
kf6-kholidays
kf6-ki18n
kf6-kiconthemes
kf6-kidletime
kf6-kimageformats
kf6-kio
kf6-kirigami
kf6-kitemmodels
kf6-kitemviews
kf6-kjobwidgets
kf6-kmime
kf6-knewstuff
kf6-knotifications
kf6-knotifyconfig
kf6-kpackage
kf6-kparts
kf6-kpeople
kf6-kplotting
kf6-kpty
kf6-kquickcharts
kf6-krunner
kf6-kservice
kf6-kstatusnotifieritem
kf6-ksvg
kf6-ktexteditor
kf6-ktexttemplate
kf6-ktextwidgets
kf6-kunitconversion
kf6-kuserfeedback
kf6-kwallet
kf6-kwidgetsaddons
kf6-kwindowsystem
kf6-kxmlgui
kf6-modemmanager-qt
kf6-networkmanager-qt
kf6-oxygen-icons
kf6-prison
kf6-purpose
kf6-qqc2-desktop-style
kf6-solid
kf6-sonnet
kf6-syndication
kf6-syntax-highlighting
kf6-threadweaver
kglobalacceld
kinfocenter
kmenuedit
knighttime
kpipewire
krdp
kscreen
kscreenlocker
ksshaskpass
ksystemstats
kwayland
kwayland-integration
kwin
kwrited
layer-shell-qt
libkscreen
libksysguard
libplasma
ocean-sound-theme
oxygen-sounds
pam-kwallet
plasma-activities
plasma-activities-stats
plasma-bigscreen
plasma-breeze
plasma-browser-integration
plasma-desktop
plasma-discover
plasma-disks
plasma-drkonqi
plasma-firewall
plasma-integration
plasma-keyboard
plasma-login-manager
plasma-milou
plasma-nano
plasma-nm
plasma-oxygen
plasma-pa
plasma-print-manager
plasma-sdk
plasma-setup
plasma-systemmonitor
plasma-systemsettings
plasma-thunderbolt
plasma-union
plasma-vault
plasma-wayland-protocols
plasma-welcome
plasma-workspace
plasma-workspace-wallpapers
plasma5support
plymouth-kcm
plymouth-theme-breeze
polkit-kde
powerdevil
qqc2-breeze-style
repodata
sddm-kcm
spectacle
xdg-desktop-portal-kde
)

packages+=(buildroot repodata)

now=$(date +%s)

for pkg in "${packages[@]}"; do
    gh api --paginate -H "Accept: application/vnd.github+json" \
        "/users/$REPOSITORY_OWNER/packages/container/$REPOSITORY%2F$pkg/versions" | \
    jq -c '.[]' | while read -r version; do

        id=$(echo "$version" | jq -r '.id')
        tags=$(echo "$version" | jq -r '.metadata.container.tags | join(",")')
        updated_at=$(echo "$version" | jq -r '.updated_at')
        updated_epoch=$(date -d "$updated_at" +%s)
        age_days=$(( (now - updated_epoch) / 86400 ))
        pkgname="${pkg//%2F//}"

        should_delete=false

        if [[ -z "$tags" ]]; then
          echo "🗑 Deleting untagged $pkgname $id"
          should_delete=true
        fi

        if echo "$tags" | grep -Eq '\bpr-[0-9]+\b'; then
          pr_num=$(echo "$tags" | grep -o '[0-9]\+')
          state=$(gh api "/repos/$REPOSITORY_OWNER/$REPOSITORY/pulls/$pr_num" -q '.state')
          if [[ "$state" == "closed" ]]; then
            echo "🗑 Deleting PRs artifacts: $pkgname $tags (ID: $id)"
            should_delete=true
          else
            echo "⏩ Skipping PR-$pr_num (still open) in $pkgname: $tags"
            continue
          fi
        fi

        if [[ "$tags" =~ latest-[[:alpha:]]+-[0-9]{2} ]]; then
          # echo "⏩ Skipping: $pkgname $tags (contains 'latest')"
          continue
        fi


        if [[ "$age_days" -ge "$DAYS_OLD" ]]; then
          echo "🗑 Deleting: $pkgname $tags (ID: $id, $age_days days old)"
          should_delete=true
        fi

        if [[ "$should_delete" == true ]]; then
            if [[ "$DRY_RUN" == "true" ]]; then
                echo "💤 Dry-run: would delete /users/$REPOSITORY_OWNER/packages/container/$REPOSITORY%2F$pkg/versions/$id"
            else
                gh api -X DELETE "/users/$REPOSITORY_OWNER/packages/container/$REPOSITORY%2F$pkg/versions/$id"
            fi
        fi
    done
done

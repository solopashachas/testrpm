#!/usr/bin/bash
while getopts 'd' opt; do
    case "$opt" in
        d) DRY_RUN=true ;;
        *) echo "Usage $0 [-d]" >&2; exit 1
    esac
done
shift "$((OPTIND-1))"

export GH_PAGER=
DAYS_OLD=7

packages=(
accessibility-inspector
akonadi-calendar
akonadi-calendar-tools
akonadi-contacts
akonadi-import-wizard
akonadi-mime
akonadi-search
akonadi-server
akonadiconsole
akregator
alligator
angelfish
arianna
ark
artikulate
audex
audiocd-kio
audiotube
aurorae
baloo-widgets
blinken
bluedevil
breeze-gtk
calendarsupport
calindori
colord-kde
dolphin
dolphin-plugins
dragon
elisa-player
eventviews
extra-cmake-modules
falkon
ffmpegthumbs
filelight
flatpak-kcm
francis
ghostwriter
grantlee-editor
grantleetheme
grub2-breeze-theme
gwenview
incidenceeditor
isoimagewriter
itinerary
juk
k3b
kaccounts-integration
kaccounts-providers
kactivitymanagerd
kaddressbook
kajongg
kalarm
kalk
kalm
kamera
kamoso
kapptemplate
kasts
kate
kbackup
kbruch
kcachegrind
kcalc
kcalutils
kcharselect
kclock
kcolorchooser
kcron
kde-cli-tools
kde-connect
kde-dev-scripts
kde-dev-utils
kde-gtk-config
kde-inotify-survey
kde-partitionmanager
kdebugsettings
kdecoration
kdegraphics-mobipocket
kdegraphics-thumbnailers
kdenetwork-filesharing
kdepim-addons
kdepim-runtime
kdeplasma-addons
kdesdk-kioslaves
kdesdk-thumbnailers
kdevelop
kdevelop-php
kdevelop-python
kdf
kdialog
kdnssd
keditbookmarks
keysmith
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
kfind
kgeography
kget
kglobalacceld
kgpg
kgraphviewer
khealthcertificate
khelpcenter
kidentitymanagement
kig
kimagemapeditor
kimap
kinfocenter
kio-admin
kio-extras
kio-gdrive
kirigami-gallery
kiten
kitinerary
kjournald
kldap
kleopatra
klettres
kmag
kmail
kmail-account-wizard
kmailtransport
kmbox
kmenuedit
kmix
kmousetool
kmouth
kmplot
knighttime
koko
kolourpaint
kompare
kongress
konqueror
konsole
kontact
kontactinterface
kontrast
konversation
kopeninghours
korganizer
kosmindoormap
kpimtextedit
kpipewire
kpkpass
kpmcore
kpublictransport
kqtquickcharts
krdc
krdp
krecorder
krfb
kruler
ksanecore
kscreen
kscreenlocker
ksmtp
ksshaskpass
ksystemlog
ksystemstats
kteatime
ktimer
ktnef
ktorrent
ktouch
ktrip
kturtle
kunifiedpush
kwalletmanager5
kwave
kwayland
kwayland-integration
kweather
kweathercore
kwin
kwrited
layer-shell-qt
libgravatar
libkcddb
libkcompactdisc
libkdcraw
libkdepim
libkexiv2
libkgapi
libkleo
libkomparediff2
libksane
libkscreen
libksieve
libksysguard
libktorrent
libplasma
lokalize
mailcommon
mailimporter
markdownpart
massif-visualizer
mbox-importer
merkuro
messagelib
mimetreeparser
neochat
ocean-sound-theme
okular
oxygen-sounds
pam-kwallet
pim-data-exporter
pim-sieve-editor
pimcommon
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
plasmatube
plymouth-kcm
plymouth-theme-breeze
polkit-kde
powerdevil
poxml
qmlkonsole
qqc2-breeze-style
qrca
rocs
sddm-kcm
signon-kwallet-extension
skanlite
skanpage
spectacle
step
svgpart
sweeper
telly-skout
tokodon
xdg-desktop-portal-kde
yakuake
zanshin
)

packages+=(buildroot)

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
          pr_num=$(echo "$tags" | grep -oP '(?<=^pr-)\d+(?=-\d+$)')
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

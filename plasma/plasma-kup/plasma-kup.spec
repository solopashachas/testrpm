%global commit0 c886a5496794350d5c233631bdd2dd9d18113d48
%global shortcommit0 %{sub %{commit0} 1 7}
%global bumpver 1

%global base_name kup

Name:           plasma-kup
Version:        6.7.90
Release:        1%{?dist}
Summary:        Backup scheduler for the Plasma desktop

License:        CC0-1.0 AND GPL-2.0-only AND GPL-2.0-or-later AND GPL-3.0-only
URL:            https://invent.kde.org/plasma/kup
%plasma_source

BuildRequires:  desktop-file-utils
BuildRequires:  libappstream-glib

BuildRequires:  cmake(Qt6Core)
BuildRequires:  cmake(Qt6Widgets)

BuildRequires:  cmake(KF6Solid)
BuildRequires:  cmake(KF6KIO)
BuildRequires:  cmake(KF6IdleTime)
BuildRequires:  cmake(KF6I18n)
BuildRequires:  cmake(KF6Notifications)
BuildRequires:  cmake(KF6CoreAddons)
BuildRequires:  cmake(KF6DBusAddons)
BuildRequires:  cmake(KF6Config)
BuildRequires:  cmake(KF6JobWidgets)
BuildRequires:  cmake(KF6WidgetsAddons)
BuildRequires:  cmake(KF6XmlGui)
BuildRequires:  cmake(KF6KCMUtils)
BuildRequires:  cmake(KF6Crash)
BuildRequires:  cmake(KF6WindowSystem)

BuildRequires:  cmake(Plasma)
BuildRequires:  pkgconfig(libgit2)

Requires:       hicolor-icon-theme

%description
Kup is created for helping people to keep up-to-date backups
of their personal files. Connecting a USB hard drive is the
primary supported way to store files, but saving files to a
server over a network connection is also possible for
advanced users.


%check
desktop-file-validate %{buildroot}/%{_kf6_datadir}/applications/*.desktop
appstream-util validate-relax --nonet %{buildroot}%{_kf6_metainfodir}/org.kde.kup.appdata.xml

%files -f %{name}.lang
%license LICENSES/*
%doc MAINTAINER README.md
%{_kf6_bindir}/kup-*
%{_kf6_plugindir}/kfileitemaction/kupfileitemaction.so
%{_kf6_plugindir}/kio/kio_bup.so
%{_kf6_qtplugindir}/plasma/applets/org.kde.kupapplet.so
%{_kf6_qtplugindir}/plasma/kcms/systemsettings_qwidgets/kcm_kup.so
%{_kf6_datadir}/applications/kcm_kup.desktop
%{_kf6_datadir}/applications/kup-daemon.desktop
%{_kf6_datadir}/icons/hicolor/scalable/apps/kup.svg
%{_kf6_datadir}/knotifications6/kupdaemon.notifyrc
%{_kf6_metainfodir}/org.kde.kup.appdata.xml
%{_kf6_datadir}/qlogging-categories6/kup.categories
%{_sysconfdir}/xdg/autostart/kup-daemon.desktop

%changelog
* Thu Sep 10 2026 Zakir Zamirov <268826384+solopashachas@users.noreply.github.com> - 6.7.90-1
- Update to 6.7.90


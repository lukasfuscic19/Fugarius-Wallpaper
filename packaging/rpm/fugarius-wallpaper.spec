Name:           fugarius-wallpaper
Version:        0.2.0
Release:        1%{?dist}
Summary:        Multi-monitor panorama wallpaper for KDE Plasma (Wayland)

License:        MIT
URL:            https://github.com/lukasfuscic19/Fugarius-Wallpaper
Source0:        %{name}-%{version}.tar.gz

BuildArch:      noarch
BuildRequires:  python3
Requires:       python3
Requires:       python3-pillow
Requires:       python3-tkinter
Requires:       kscreen
Requires:       qt6-qttools
Recommends:     swww

%description
Fugarius Wallpaper places one source image across all monitors as a single
virtual-desktop panorama. Per-monitor zoom, offset, and rotation; native
wallpaper apply on KDE Plasma via D-Bus (qdbus, kscreen-doctor).

%prep
%autosetup -n Fugarius-Wallpaper-%{version}

%build
# noarch Python GUI — nothing to compile

%install
install -d %{buildroot}%{_datadir}/fugarius-wallpaper
install -m 644 app.py requirements.txt README.md LICENSE %{buildroot}%{_datadir}/fugarius-wallpaper/
install -m 755 restore_panels.sh verify_panorama.py %{buildroot}%{_datadir}/fugarius-wallpaper/

install -d %{buildroot}%{_bindir}
install -m 755 packaging/fugarius-wallpaper.sh %{buildroot}%{_bindir}/fugarius-wallpaper

install -d %{buildroot}%{_datadir}/applications
install -m 644 packaging/fugarius-wallpaper.desktop %{buildroot}%{_datadir}/applications/

install -d %{buildroot}%{_datadir}/metainfo
install -m 644 packaging/com.fugarius.Wallpaper.appdata.xml %{buildroot}%{_datadir}/metainfo/

for size in 16 22 24 32 48 64 128 256 512; do
  icon="assets/icons/hicolor/${size}x${size}/apps/fugarius-wallpaper.png"
  if test -f "${icon}"; then
    install -d %{buildroot}%{_datadir}/icons/hicolor/${size}x${size}/apps
    install -m 644 "${icon}" %{buildroot}%{_datadir}/icons/hicolor/${size}x${size}/apps/
  fi
done

%files
%license LICENSE
%doc README.md
%{_bindir}/fugarius-wallpaper
%{_datadir}/fugarius-wallpaper/
%{_datadir}/applications/fugarius-wallpaper.desktop
%{_datadir}/metainfo/com.fugarius.Wallpaper.appdata.xml
%{_datadir}/icons/hicolor/*/apps/fugarius-wallpaper.png

%changelog
* Tue Jun 03 2026 Lukas Fuscic <lukasfuscic19@users.noreply.github.com> - 0.2.0-1
- Panorama centering, per-monitor transforms, icons, RPM packaging

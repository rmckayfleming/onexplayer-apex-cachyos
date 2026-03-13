# After reboot (ba28) — build and test oxpec.ko

## 1. Build oxpec.ko for ba28
```bash
cd ~/Work/onexplayer-apex-bazzite-fixes/decky-plugin/py_modules/oxpec/build
make
mkdir -p ../6.17.7-ba28.fc43.x86_64
cp oxpec.ko ../6.17.7-ba28.fc43.x86_64/
```

## 2. Test
```bash
cd ~/Work/onexplayer-apex-bazzite-fixes/decky-plugin
bun run install-plugin
```

## 3. Verify
- Plugin should auto-load oxpec via insmod with the ba28 .ko
- Check fan control works in HHD
- Check plugin UI shows "Loaded (bundled)"

## Rollback if needed
```bash
sudo rpm-ostree rollback
systemctl reboot
```

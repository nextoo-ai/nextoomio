# nextoomio base-files configuration (AC2, P0-F2.4, P0-F2.3)
# Enforces device hostname 'nextoomio', standard local hosts mapping, and /data mount

hostname = "nextoomio"

do_install:append() {
    # Ensure hostname is nextoomio
    echo "nextoomio" > ${D}${sysconfdir}/hostname

    # Local host resolution mapping for nextoomio and nextoomio.local
    cat << 'EOF' > ${D}${sysconfdir}/hosts
127.0.0.1   localhost nextoomio nextoomio.local
::1         localhost ip6-localhost ip6-loopback nextoomio nextoomio.local
EOF

    # Ensure persistent /data mount point exists (ARCHITECTURE.md section 3.5, AC3)
    install -d -m 0755 ${D}/data

    # Persistent /data partition mount in fstab (partition 7)
    if ! grep -q "^LABEL=data" ${D}${sysconfdir}/fstab 2>/dev/null; then
        echo "LABEL=data /data ext4 defaults,noatime 0 2" >> ${D}${sysconfdir}/fstab
    fi
}

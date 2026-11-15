FILESEXTRAPATHS:prepend := "${THISDIR}/files:"

# Ensure mount point directories exist in the base filesystem (ARCHITECTURE.md sections 3.5, 3.6)
dirs755:append = " /boot/select /data"

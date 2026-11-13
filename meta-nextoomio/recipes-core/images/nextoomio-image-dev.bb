require nextoomio-image.bb

SUMMARY = "nextoomio development image with SSH and diagnostic tools"
DESCRIPTION = "Development variant enabling OpenSSH server and diagnostic utilities (ARCHITECTURE.md section 3.7)."

IMAGE_FEATURES += "ssh-server-openssh"

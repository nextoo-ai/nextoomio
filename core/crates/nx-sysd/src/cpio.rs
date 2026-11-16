//! CPIO archive reader and builder for SWUpdate `.swu` compound packages.
//!
//! Implements SVR4 portable "new ASCII" CPIO format (magic 070701 / 070702).

use std::fmt;
use std::io::{self, Read};

/// Standard CPIO new ASCII magic bytes.
pub const CPIO_MAGIC_NEW_ASCII: &[u8; 6] = b"070701";
pub const CPIO_MAGIC_NEW_ASCII_CRC: &[u8; 6] = b"070702";

/// Trailer filename indicating end of archive.
pub const CPIO_TRAILER_NAME: &str = "TRAILER!!!";

/// Errors occurring during CPIO parsing.
#[derive(Debug, PartialEq, Eq)]
pub enum CpioError {
    InvalidMagic,
    InvalidHeader,
    Truncated,
    Io(String),
}

impl fmt::Display for CpioError {
    fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
        match self {
            Self::InvalidMagic => {
                f.write_str("invalid CPIO magic header (expected 070701 or 070702)")
            }
            Self::InvalidHeader => f.write_str("malformed CPIO entry header fields"),
            Self::Truncated => f.write_str("CPIO archive ends prematurely or is truncated"),
            Self::Io(msg) => write!(f, "I/O error during CPIO parsing: {msg}"),
        }
    }
}

impl std::error::Error for CpioError {}

impl From<io::Error> for CpioError {
    fn from(err: io::Error) -> Self {
        if err.kind() == io::ErrorKind::UnexpectedEof {
            Self::Truncated
        } else {
            Self::Io(err.to_string())
        }
    }
}

/// Extracted entry from a CPIO archive.
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct CpioEntry {
    pub name: String,
    pub mode: u32,
    pub data: Vec<u8>,
}

fn parse_hex_u32(slice: &[u8]) -> Result<u32, CpioError> {
    let s = std::str::from_utf8(slice).map_err(|_| CpioError::InvalidHeader)?;
    u32::from_str_radix(s, 16).map_err(|_| CpioError::InvalidHeader)
}

fn read_exact_or_truncated<R: Read>(reader: &mut R, buf: &mut [u8]) -> Result<(), CpioError> {
    match reader.read_exact(buf) {
        Ok(()) => Ok(()),
        Err(err) if err.kind() == io::ErrorKind::UnexpectedEof => Err(CpioError::Truncated),
        Err(err) => Err(CpioError::Io(err.to_string())),
    }
}

/// Reads all entries from a CPIO archive stream.
pub fn read_cpio_entries<R: Read>(mut reader: R) -> Result<Vec<CpioEntry>, CpioError> {
    let mut entries = Vec::new();
    let mut header_buf = [0u8; 110];

    loop {
        // Read 110-byte fixed header
        match reader.read_exact(&mut header_buf) {
            Ok(()) => {}
            Err(err) if err.kind() == io::ErrorKind::UnexpectedEof => {
                // If EOF at start of entry and we have read at least one entry, it might be truncated trailer
                if entries.is_empty() {
                    return Err(CpioError::Truncated);
                }
                return Err(CpioError::Truncated);
            }
            Err(err) => return Err(CpioError::Io(err.to_string())),
        }

        let magic = &header_buf[0..6];
        if magic != CPIO_MAGIC_NEW_ASCII && magic != CPIO_MAGIC_NEW_ASCII_CRC {
            return Err(CpioError::InvalidMagic);
        }

        let mode = parse_hex_u32(&header_buf[14..22])?;
        let filesize = parse_hex_u32(&header_buf[54..62])? as usize;
        let namesize = parse_hex_u32(&header_buf[94..102])? as usize;

        if namesize == 0 {
            return Err(CpioError::InvalidHeader);
        }

        // Read filename
        let mut name_buf = vec![0u8; namesize];
        read_exact_or_truncated(&mut reader, &mut name_buf)?;

        // Padding to 4 bytes: (110 + namesize + pad1) % 4 == 0
        let pad1 = (4 - ((110 + namesize) % 4)) % 4;
        if pad1 > 0 {
            let mut pad_buf = vec![0u8; pad1];
            read_exact_or_truncated(&mut reader, &mut pad_buf)?;
        }

        // Null-terminated string conversion
        let name_trimmed = if name_buf.last() == Some(&0) {
            &name_buf[..name_buf.len() - 1]
        } else {
            &name_buf[..]
        };
        let name =
            String::from_utf8(name_trimmed.to_vec()).map_err(|_| CpioError::InvalidHeader)?;

        if name == CPIO_TRAILER_NAME {
            break;
        }

        // Read file contents
        let mut data_buf = vec![0u8; filesize];
        read_exact_or_truncated(&mut reader, &mut data_buf)?;

        // Padding to 4 bytes: (filesize + pad2) % 4 == 0
        let pad2 = (4 - (filesize % 4)) % 4;
        if pad2 > 0 {
            let mut pad_buf = vec![0u8; pad2];
            read_exact_or_truncated(&mut reader, &mut pad_buf)?;
        }

        entries.push(CpioEntry {
            name,
            mode,
            data: data_buf,
        });
    }

    Ok(entries)
}

/// Helper function to construct a CPIO archive from named byte entries.
pub fn create_cpio(entries: &[(&str, &[u8])]) -> Vec<u8> {
    let mut out = Vec::new();

    for (name, data) in entries {
        append_cpio_entry(&mut out, name, 0o100644, data);
    }
    // Append standard TRAILER!!! entry
    append_cpio_entry(&mut out, CPIO_TRAILER_NAME, 0, &[]);

    // Trailer padding to 512 bytes is optional in SVR4 cpio, but align to 4 bytes
    let rem = out.len() % 4;
    if rem > 0 {
        out.extend(vec![0u8; 4 - rem]);
    }

    out
}

fn append_cpio_entry(out: &mut Vec<u8>, name: &str, mode: u32, data: &[u8]) {
    let name_bytes = name.as_bytes();
    let namesize = name_bytes.len() + 1; // including terminating null
    let filesize = data.len();

    // 110-byte header
    let header = format!(
        "070701{:08x}{:08x}{:08x}{:08x}{:08x}{:08x}{:08x}{:08x}{:08x}{:08x}{:08x}{:08x}{:08x}",
        0, // ino
        mode,
        0, // uid
        0, // gid
        1, // nlink
        0, // mtime
        filesize,
        0, // devmajor
        0, // devminor
        0, // rdevmajor
        0, // rdevminor
        namesize,
        0 // check
    );

    out.extend_from_slice(header.as_bytes());
    out.extend_from_slice(name_bytes);
    out.push(0); // terminating null

    let pad1 = (4 - ((110 + namesize) % 4)) % 4;
    out.extend(vec![0u8; pad1]);

    out.extend_from_slice(data);
    let pad2 = (4 - (filesize % 4)) % 4;
    out.extend(vec![0u8; pad2]);
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn test_round_trip_cpio() {
        let entries = [
            ("sw-description", b"software = {};".as_slice()),
            ("boot.img.gz", b"fake boot image data 123456789".as_slice()),
            ("root.ext4.gz", b"fake rootfs ext4 gzipped data".as_slice()),
        ];

        let archive = create_cpio(&entries);
        let parsed = read_cpio_entries(archive.as_slice()).expect("parse failed");

        assert_eq!(parsed.len(), 3);
        assert_eq!(parsed[0].name, "sw-description");
        assert_eq!(parsed[0].data, b"software = {};");
        assert_eq!(parsed[1].name, "boot.img.gz");
        assert_eq!(parsed[1].data, b"fake boot image data 123456789");
        assert_eq!(parsed[2].name, "root.ext4.gz");
        assert_eq!(parsed[2].data, b"fake rootfs ext4 gzipped data");
    }

    #[test]
    fn test_truncated_header_fails_with_truncated() {
        let entries = [("sw-description", b"test".as_slice())];
        let archive = create_cpio(&entries);

        // Truncate in the middle of header
        let truncated = &archive[..50];
        let err = read_cpio_entries(truncated).expect_err("should fail");
        assert_eq!(err, CpioError::Truncated);
    }

    #[test]
    fn test_truncated_data_fails_with_truncated() {
        let entries = [(
            "sw-description",
            b"long string with data that gets cut".as_slice(),
        )];
        let archive = create_cpio(&entries);

        // Truncate right before end of file data
        let truncated = &archive[..110 + 15 + 10];
        let err = read_cpio_entries(truncated).expect_err("should fail");
        assert_eq!(err, CpioError::Truncated);
    }

    #[test]
    fn test_invalid_magic_fails() {
        let mut archive = create_cpio(&[("test", b"abc")]);
        archive[0..6].copy_from_slice(b"123456");
        let err = read_cpio_entries(archive.as_slice()).expect_err("should fail");
        assert_eq!(err, CpioError::InvalidMagic);
    }
}

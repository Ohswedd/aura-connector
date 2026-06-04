//! Native acceleration for the Aura connector.
//!
//! This crate is an *optional* speed path for a few hot, well-bounded operations:
//! CRC32 (protocol frame checksums) and fixed-dimension f32 vector packing. It is
//! always shadowed by an identical pure-Python implementation in
//! `aura._native`; the pure-Python path is the reference behaviour and is used
//! whenever this extension is not installed (see `docs/NATIVE_ACCELERATION.md`).
//!
//! Parity contract (verified by `tests/native/test_native_parity.py`):
//! * `native_crc32` equals `zlib.crc32(data) & 0xFFFFFFFF`.
//! * vector components are validated as finite and exactly `dimension` long.
//! * packed bytes are contiguous little-endian IEEE-754 f32, `4 * dimension` long.

use pyo3::exceptions::PyValueError;
use pyo3::prelude::*;
use pyo3::types::PyBytes;

/// CRC32 (ISO-HDLC, the same polynomial and reflection as `zlib.crc32`).
#[pyfunction]
fn native_crc32(data: &[u8]) -> u32 {
    let mut hasher = crc32fast::Hasher::new();
    hasher.update(data);
    hasher.finalize()
}

/// Validate `values` as a finite, `dimension`-length float vector.
fn validate(values: &[f64], dimension: usize) -> PyResult<Vec<f64>> {
    if dimension == 0 {
        return Err(PyValueError::new_err(
            "Vector dimension must be a positive int, got 0",
        ));
    }
    if values.len() != dimension {
        return Err(PyValueError::new_err(format!(
            "Vector expected dimension {dimension}, got {}",
            values.len()
        )));
    }
    for &v in values {
        if !v.is_finite() {
            return Err(PyValueError::new_err(
                "Vector components must be finite numbers",
            ));
        }
    }
    Ok(values.to_vec())
}

#[pyfunction]
fn native_validate_vector(values: Vec<f64>, dimension: usize) -> PyResult<Vec<f64>> {
    validate(&values, dimension)
}

/// Validate then pack into contiguous little-endian f32 bytes.
#[pyfunction]
fn native_pack_f32_vector(py: Python<'_>, values: Vec<f64>, dimension: usize) -> PyResult<Py<PyBytes>> {
    let validated = validate(&values, dimension)?;
    let mut out = Vec::with_capacity(dimension * 4);
    for v in validated {
        out.extend_from_slice(&(v as f32).to_le_bytes());
    }
    Ok(PyBytes::new_bound(py, &out).into())
}

/// Unpack contiguous little-endian f32 bytes into f64 components.
#[pyfunction]
fn native_unpack_f32_vector(data: &[u8], dimension: usize) -> PyResult<Vec<f64>> {
    if dimension == 0 {
        return Err(PyValueError::new_err(
            "Vector dimension must be a positive int, got 0",
        ));
    }
    let expected = dimension * 4;
    if data.len() != expected {
        return Err(PyValueError::new_err(format!(
            "Packed vector expected {expected} bytes, got {}",
            data.len()
        )));
    }
    let mut out = Vec::with_capacity(dimension);
    for chunk in data.chunks_exact(4) {
        let bytes: [u8; 4] = chunk.try_into().expect("chunk is exactly 4 bytes");
        out.push(f32::from_le_bytes(bytes) as f64);
    }
    Ok(out)
}

/// Backend identity string, mirrored by `aura._native.native_backend_name`.
#[pyfunction]
fn backend_name() -> &'static str {
    "aura_native"
}

#[pymodule]
fn aura_native(m: &Bound<'_, PyModule>) -> PyResult<()> {
    m.add("__version__", env!("CARGO_PKG_VERSION"))?;
    m.add_function(wrap_pyfunction!(native_crc32, m)?)?;
    m.add_function(wrap_pyfunction!(native_validate_vector, m)?)?;
    m.add_function(wrap_pyfunction!(native_pack_f32_vector, m)?)?;
    m.add_function(wrap_pyfunction!(native_unpack_f32_vector, m)?)?;
    m.add_function(wrap_pyfunction!(backend_name, m)?)?;
    Ok(())
}

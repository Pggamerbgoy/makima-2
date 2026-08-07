//! Makima v7.1 — Rust Core Library
//!
//! PyO3 module registration. Exposes:
//! - VectorIndex (HNSW)
//! - TripleStore (SQLite WAL)
//! - Checkpointer (CBOR atomic write)
//! - FileIndexer (hash-based re-indexing)

use pyo3::prelude::*;

mod vector_index;
mod triple_store;
mod checkpoint;

use vector_index::VectorIndex;
use triple_store::TripleStore;
use checkpoint::Checkpointer;

/// The Python module exposed as `makima_core`
#[pymodule]
fn makima_core(m: &Bound<'_, PyModule>) -> PyResult<()> {
    m.add_class::<VectorIndex>()?;
    m.add_class::<TripleStore>()?;
    m.add_class::<Checkpointer>()?;
    Ok(())
}

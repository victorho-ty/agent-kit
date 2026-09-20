"""Shipped data: what bootstraps an empty database, and the vocabulary."""

from .seeds import SEEDS_FILE, load_seeds
from .taxonomy import TAXONOMY_FILE, Taxonomy, load_taxonomy

__all__ = ["SEEDS_FILE", "TAXONOMY_FILE", "Taxonomy", "load_seeds", "load_taxonomy"]

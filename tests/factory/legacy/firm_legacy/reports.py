"""A function that writes a file: a facade that declares write_local_data."""


def export_positions(positions, path):
    """Write the positions to a CSV file."""
    positions.to_csv(path)

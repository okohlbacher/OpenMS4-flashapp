"""Exercise ordinary app dependencies from requirements.txt; no pyOpenMS needed."""
from pathlib import Path
import tempfile

import numpy as np
import pandas as pd
import polars as pl
import pyarrow as pa
import pyarrow.dataset as ds
import pyarrow.parquet as pq
import scipy.linalg


def test_pandas_arrow_polars_parquet_exchange():
    frame = pd.DataFrame({'value': np.array([1., 2., 3.])})
    with tempfile.TemporaryDirectory() as directory:
        path = Path(directory) / 'data.parquet'
        pq.write_table(pa.Table.from_pandas(frame), path)
        assert ds.dataset(path).to_table().num_rows == 3
        assert pl.read_parquet(path)['value'].sum() == 6.0
    assert scipy.linalg.norm(frame['value'].to_numpy()) > 0

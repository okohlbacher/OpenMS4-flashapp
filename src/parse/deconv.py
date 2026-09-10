import pandas as pd
import polars as pl
import numpy as np

from src.parse.masstable import parseFLASHDeconvOutput, getMSSignalDF, getSpectraTableDF
from src.render.compression import downsample_heatmap, compute_compression_levels
from scipy.stats import gaussian_kde

# One row per scan with heavy array-valued cells; small row groups so per-scan
# pushdown reads only the matching group(s) instead of the whole file.
SPECTRA_ROW_GROUP_SIZE = 64

def parseDeconv(
        file_manager, dataset_id, out_deconv_mzML, anno_annotated_mzML, 
        spec1_tsv=None, spec2_tsv=None, logger=None
):
    logger.log("Progress of 'processing FLASHDeconv results':", level=2)
    logger.log("0.0 %", level=2)

    # Parse input files
    tolerance = parseFLASHDeconvOutput(
        anno_annotated_mzML, out_deconv_mzML,
        file_manager, dataset_id, logger=logger,
    )
    file_manager.store_data(dataset_id, 'deconv_tolerance', float(tolerance))
    
    # Immediately reload as polars LazyFrames for efficient processing
    results = file_manager.get_results(dataset_id, ['anno_dfs', 'deconv_dfs'], use_polars=True)
    pl_anno = results['anno_dfs']
    pl_deconv = results['deconv_dfs']
    
    logger.log("10.0 %", level=2)

    # Preprocess data for the heatmaps
    for df, descriptor in zip([pl_deconv, pl_anno], ['deconv', 'raw']):

        # Create full sized version - returns polars LazyFrame
        heatmap_lazy = getMSSignalDF(df)

        for ms_level in [1, 2]:
            
            # Filter for specific MS level using polars operations
            relevant_heatmap_lazy = (
                heatmap_lazy
                .filter(pl.col('MSLevel') == ms_level)
                .drop('MSLevel')
            )

            # Collect here as this is the data we are operating on
            relevant_heatmap_lazy = relevant_heatmap_lazy.collect().lazy()

            # Get count for compression level calculation
            heatmap_count = relevant_heatmap_lazy.select(pl.len()).collect().item()

            # Store full sized version
            file_manager.store_data(
                dataset_id, f'ms{ms_level}_{descriptor}_heatmap',
                relevant_heatmap_lazy
            )

            # Store compressed versions
            compression_levels = compute_compression_levels(20000, heatmap_count, logger=logger)
            current_heatmap_lazy = relevant_heatmap_lazy
            
            for size in reversed(compression_levels):
                # Downsample iteratively using polars-optimized function
                current_heatmap_lazy = downsample_heatmap(current_heatmap_lazy, max_datapoints=size)
                
                # Store compressed version - convert to pandas only at storage
                file_manager.store_data(
                    dataset_id, f'ms{ms_level}_{descriptor}_heatmap_{size}',
                    current_heatmap_lazy
                )
    
    logger.log("20.0 %", level=2)
        
    # scan_table - using native polars operations
    spectra_lazy = (
        pl_deconv
        .with_row_index("index")
        .with_columns([
            pl.col('MinCharges').list.len().alias('#Masses')
        ])
        .select([
            pl.col('index'),
            pl.col('Scan'),
            pl.col('MSLevel'),
            pl.col('rt').alias('RT'),
            pl.col('PrecursorMass'),
            pl.col('#Masses')
        ])
        .sort("index")
    )
    file_manager.store_data(dataset_id, 'scan_table', spectra_lazy)

    logger.log("30.0 %", level=2)

    # Add row indices for joining operations
    pl_deconv_indexed = pl_deconv.with_row_index("index")
    pl_anno_indexed = pl_anno.with_row_index("index")

    # anno_spectrum - using native polars LazyFrame operations
    anno_spectrum_lazy = (
        pl_anno_indexed
        .select([
            pl.col('index'),
            pl.col('mz_array').alias('MonoMass_Anno'),
            pl.col('intensity_array').alias('SumIntensity_Anno')
        ])
        .sort("index")
    )
    file_manager.store_data(dataset_id, 'anno_spectrum', anno_spectrum_lazy, row_group_size=SPECTRA_ROW_GROUP_SIZE)

    logger.log("40.0 %", level=2)

    # mass_table - using native polars LazyFrame operations
    mass_table_lazy = (
        pl_deconv_indexed
        .select([
            pl.col('index'),
            pl.col('mz_array').alias('MonoMass'),
            pl.col('intensity_array').alias('SumIntensity'),
            pl.col('MinCharges'),
            pl.col('MaxCharges'),
            pl.col('MinIsotopes'),
            pl.col('MaxIsotopes'),
            pl.col('cos').alias('CosineScore'),
            pl.col('snr').alias('SNR'),
            pl.col('qscore').alias('QScore')
        ])
        .sort("index")
    )
    file_manager.store_data(dataset_id, 'mass_table', mass_table_lazy, row_group_size=SPECTRA_ROW_GROUP_SIZE)

    logger.log("50.0 %", level=2)

    # sequence_view - using native polars LazyFrame operations
    sequence_view_lazy = (
        pl_deconv_indexed
        .select([
            pl.col('index'),
            pl.col('mz_array').alias('MonoMass'),
            pl.col('PrecursorMass')
        ])
        .sort("index")
    )
    file_manager.store_data(dataset_id, 'sequence_view', sequence_view_lazy, row_group_size=SPECTRA_ROW_GROUP_SIZE)

    logger.log("60.0 %", level=2)

    # deconv_spectrum - using native polars LazyFrame operations
    deconv_spectrum_lazy = (
        pl_deconv_indexed
        .select([
            pl.col('index'),
            pl.col('mz_array').alias('MonoMass'),
            pl.col('intensity_array').alias('SumIntensity')
        ])
        .sort("index")
    )
    file_manager.store_data(dataset_id, 'deconv_spectrum', deconv_spectrum_lazy, row_group_size=SPECTRA_ROW_GROUP_SIZE)

    logger.log("70.0 %", level=2)

    # anno & deconv spectrum (combined_spectrum) - using native polars LazyFrame join
    combined_spectrum_lazy = (
        pl_deconv_indexed
        .select([
            pl.col('index'),
            pl.col('mz_array').alias('MonoMass'),
            pl.col('intensity_array').alias('SumIntensity'),
            pl.col('SignalPeaks')
        ])
        .join(
            pl_anno_indexed.select([
                pl.col('index'),
                pl.col('mz_array').alias('MonoMass_Anno'),
                pl.col('intensity_array').alias('SumIntensity_Anno')
            ]),
            on='index',
            how='left'
        )
        .sort("index")
    )
    file_manager.store_data(dataset_id, 'combined_spectrum', combined_spectrum_lazy, row_group_size=SPECTRA_ROW_GROUP_SIZE)

    logger.log("80.0 %", level=2)

    # 3D_SN_plot - using native polars LazyFrame operations
    threedim_SN_plot_lazy = (
        pl_deconv_indexed
        .select([
            pl.col('index'),
            pl.col('PrecursorScan'),
            pl.col('SignalPeaks'),
            pl.col('NoisyPeaks')
        ])
    )
    file_manager.store_data(dataset_id, 'threedim_SN_plot', threedim_SN_plot_lazy)

    logger.log("90.0 %", level=2)

    # fdr_plot
    fdr_dfs = []
    if spec1_tsv is not None:
        fdr_dfs.append(pd.read_csv(spec1_tsv, sep='\t'))
    if spec2_tsv is not None:
        fdr_dfs.append(pd.read_csv(spec2_tsv, sep='\t'))
    if len(fdr_dfs) > 0:
        fdr_dfs = pd.concat(fdr_dfs, axis=0, ignore_index=True)
        if 'TargetDecoyType' not in fdr_dfs.columns:
            fdr_dfs['TargetDecoyType'] = 0
        density_target, density_decoy = fdr_density_distribution(fdr_dfs)
        file_manager.store_data(dataset_id, 'density_target', density_target)
        file_manager.store_data(dataset_id, 'density_decoy', density_decoy)
    
    logger.log("100.0 %", level=2)


def fdr_density_distribution(df):
    """Return target/decoy KDE curves, or empty x/y frames when undefined.

    A density estimate needs at least two distinct finite scores. Empty,
    singleton and constant groups have no estimate; do not invent a curve by
    adding jitter to scientific scores. The viewer already accepts empty curves.
    """
    densities = []
    for selection in (df['TargetDecoyType'] == 0, df['TargetDecoyType'] > 0):
        scores = df.loc[selection, 'Qscore'].dropna()
        scores = scores[np.isfinite(scores)]
        if scores.nunique() < 2:
            densities.append(pd.DataFrame(columns=['x', 'y'], dtype=float))
        else:
            x = np.linspace(scores.min(), scores.max(), 200)
            densities.append(pd.DataFrame({'x': x, 'y': gaussian_kde(scores)(x)}))
    return tuple(densities)

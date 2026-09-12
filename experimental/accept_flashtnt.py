#!/usr/bin/env python3
"""Run the installed FLASHTnT through FLASHApp against its retained AQPZ example."""
import argparse
from collections import Counter
import hashlib
import json
import math
import multiprocessing
import os
from pathlib import Path
import shutil
import sys
import time

ROOT = Path(__file__).resolve().parents[1]
FIXTURE = ROOT / 'example-data/workspaces/default/flashtnt/cache/files/example_spectrum_aqpz_20250525-203956'
RAW = ROOT / 'example-data/workspaces/default/flashtnt/input-files/mzML-files/example_spectrum_aqpz.mzML'


def check_identifications(directory):
    """Require the retained TSV interface and positive AQPZ sequence evidence."""
    import pandas as pd

    frames, reference, schema_changes = {}, {}, {}
    for name in ('tags.tsv', 'protein.tsv', 'prsms.tsv'):
        expected = pd.read_csv(FIXTURE / name, sep='\t')
        actual = pd.read_csv(directory / name, sep='\t')
        renamed = {}
        # The pinned upstream writer renamed the PrSM row index. FLASHApp
        # parses tags/proteins, but retains this table as a downloadable file.
        if name == 'prsms.tsv' and 'PrSMIndex' in actual and 'ProteoformIndex' in expected:
            renamed = {'ProteoformIndex': 'PrSMIndex'}
            expected = expected.rename(columns=renamed)
        schema_changes[name] = {'renamed': renamed, 'added': sorted(set(actual.columns) - set(expected.columns))}
        if actual.empty or not set(expected.columns).issubset(actual.columns):
            raise ValueError(f'{name}: nonempty results with the app TSV columns required')
        frames[name] = actual
        reference[name] = expected
    proteins = frames['protein.tsv']
    best = proteins.loc[proteins['Score'].idxmax()]
    expected_aqpz = reference['protein.tsv'].query('ProteinAccession == "AQPZ"').iloc[0]
    if best['ProteinAccession'] != 'AQPZ' or best['Score'] <= 0:
        raise ValueError('The AQPZ example must identify AQPZ as its highest-scoring protein')
    if best['DatabaseSequence'] != expected_aqpz['DatabaseSequence']:
        raise ValueError('The identified AQPZ sequence differs from the retained example')
    tags = frames['tags.tsv']
    aqpz_tags = tags[tags['ProteinAccession'].fillna('').str.split(';').apply(lambda values: 'AQPZ' in values)]
    if aqpz_tags.empty or not (aqpz_tags['Length'] >= 4).all():
        raise ValueError('Expected identified AQPZ tags of at least four residues')
    comparison = {name + '_rows': {'actual': len(frames[name]), 'reference': len(reference[name]),
                                   'match': len(frames[name]) == len(reference[name])}
                  for name in frames}
    actual_sequences = Counter(tags['TagSequence'])
    reference_sequences = Counter(reference['tags.tsv']['TagSequence'])
    comparison['tag_sequence_counts'] = {'match': actual_sequences == reference_sequences,
        'extra_count': sum((actual_sequences - reference_sequences).values()),
        'missing_count': sum((reference_sequences - actual_sequences).values())}
    for field in ('Score', 'PrecursorMass', 'ProteoformMass', 'MatchingFragments', 'Coverage(%)',
                  'StartPosition', 'EndPosition'):
        actual, expected = float(best[field]), float(expected_aqpz[field])
        comparison['best_' + field] = {'actual': actual, 'reference': expected,
                                       'match': math.isclose(actual, expected, rel_tol=1e-6, abs_tol=1e-6)}
    comparison['best_matched_sequence'] = {'actual': best['ProteinSequence'],
        'reference': expected_aqpz['ProteinSequence'], 'match': best['ProteinSequence'] == expected_aqpz['ProteinSequence']}
    return {'tags': len(tags), 'proteins': len(proteins), 'prsms': len(frames['prsms.tsv']),
            'aqpz_tags': len(aqpz_tags), 'top_accession': best['ProteinAccession'],
            'top_score': float(best['Score']), 'reference_comparison': comparison,
            'schema_changes': schema_changes, 'positive_scientific_checks_passed': True,
            'reference_fields_match': all(item['match'] for item in comparison.values())}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path, required=True, help='New directory for results, cache and logs')
    parser.add_argument('--raw-workflow', action='store_true', help='Run the actual TagWorkflow, including FLASHDeconv')
    parser.add_argument('--threads', type=int, default=2)
    args = parser.parse_args()
    if args.threads < 1:
        parser.error('--threads must be positive')
    tools = ['FLASHTnT', 'FLASHDeconv'] if args.raw_workflow else ['FLASHTnT']
    executables = {name: shutil.which(name) for name in tools}
    if not all(executables.values()):
        parser.error(f'Required installed tools are absent from PATH: {executables}')
    output = args.output.resolve()
    output.mkdir(parents=True, exist_ok=False)
    workflow_dir = output / 'workflow'
    workflow_dir.mkdir()
    os.chdir(ROOT)
    os.environ['FLASH_POSTPROC_WORKERS'] = str(args.threads)
    multiprocessing.set_start_method('spawn', force=True)
    sys.path.insert(0, str(ROOT))

    from src.parse.deconv import parseDeconv
    from src.parse.tnt import parseTnT
    from src.workflow.CommandExecutor import CommandExecutor
    from src.workflow.FileManager import FileManager
    from src.workflow.Logger import Logger
    from src.workflow.ParameterManager import ParameterManager

    parameters = {'max_threads': args.threads, 'few_proteins': False,
                  'FLASHDeconv': json.loads((FIXTURE / 'FD_parameters.json').read_text()),
                  'FLASHTnT': json.loads((FIXTURE / 'FTnT_parameters.json').read_text()),
                  'mzML-files': [str(RAW)], 'fasta-file': [str(FIXTURE / 'database.fasta')]}
    (workflow_dir / 'params.json').write_text(json.dumps(parameters, indent=2))
    settings = {'online_deployment': False, 'max_threads': {'local': args.threads}}
    started = time.perf_counter()
    files = FileManager(workflow_dir, output / 'cache')
    if args.raw_workflow:
        from src.workflow.tasks import execute_workflow
        result = execute_workflow(str(workflow_dir), 'TagWorkflow', 'src.Workflow', settings)
        if not result['success']:
            raise RuntimeError(result['error'])
        datasets = files.get_results_list(['protein_tsv', 'tags_tsv', 'prsms_tsv'])
        if len(datasets) != 1:
            raise ValueError(f'Expected exactly one completed workflow dataset: {datasets}')
        dataset = datasets[0]
        directory = Path(files.get_results(dataset, ['protein_tsv'])['protein_tsv']).parent
    else:
        logger = Logger(workflow_dir)
        manager = ParameterManager(workflow_dir)
        executor = CommandExecutor(workflow_dir, logger, manager, settings)
        directory = output / 'native'
        directory.mkdir()
        executor.run_topp('FLASHTnT', {
            'in': [str(FIXTURE / 'out_deconv.mzML')], 'fasta': [str(FIXTURE / 'database.fasta')],
            'out_tag': [str(directory / 'tags.tsv')], 'out_pro': [str(directory / 'protein.tsv')],
            'out_prsm': [str(directory / 'prsms.tsv')]})
        dataset = 'aqpz'
        files.store_file(dataset, 'FTnT_parameters_json', FIXTURE / 'FTnT_parameters.json', remove=False)
        parseDeconv(files, dataset, str(FIXTURE / 'out_deconv.mzML'), str(FIXTURE / 'anno_annotated.mzML'),
                    spec2_tsv=str(FIXTURE / 'spec2.tsv'), logger=logger)
        parseTnT(files, dataset, str(FIXTURE / 'out_deconv.mzML'), str(FIXTURE / 'anno_annotated.mzML'),
                 str(directory / 'tags.tsv'), str(directory / 'protein.tsv'), logger)

    report = check_identifications(directory)
    cached = files.get_results(dataset, ['protein_dfs', 'tag_dfs'])
    if cached['protein_dfs'].empty or cached['tag_dfs'].empty or not files.result_exists(dataset, 'sequence_data'):
        raise ValueError('App parser did not retain protein, tag and sequence-view results')
    report.update({'mode': 'raw-workflow' if args.raw_workflow else 'tagging-and-parsing',
                   'wall_seconds': time.perf_counter() - started, 'executables': executables,
                   'fixture_sha256': {path.name: hashlib.sha256(path.read_bytes()).hexdigest()
                                      for path in (FIXTURE / 'database.fasta', FIXTURE / 'out_deconv.mzML', RAW)}})
    (output / 'acceptance.json').write_text(json.dumps(report, indent=2) + '\n')
    print(json.dumps(report, indent=2))
    files.cache_connection.close()
    if not report['reference_fields_match']:
        raise ValueError('Scientific reference fields differ; inspect acceptance.json before qualifying this port')


if __name__ == '__main__':
    main()

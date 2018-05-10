import argparse
import datetime
import logging
import multiprocessing
import sys
import tempfile
from os import path, mkdir

import pandas as pd
import numpy as np

from staramr.SubCommand import SubCommand
from staramr.Utils import get_string_with_spacing
from staramr.blast.BlastHandler import BlastHandler
from staramr.blast.pointfinder.PointfinderBlastDatabase import PointfinderBlastDatabase
from staramr.blast.resfinder.ResfinderBlastDatabase import ResfinderBlastDatabase
from staramr.databases.AMRDatabasesManager import AMRDatabasesManager
from staramr.databases.resistance.ARGDrugTable import ARGDrugTable
from staramr.detection.AMRDetectionFactory import AMRDetectionFactory
from staramr.exceptions.CommandParseException import CommandParseException

logger = logging.getLogger("Search")

"""
Class for searching for AMR resistance genes.
"""


class Search(SubCommand):
    BLANK = '-'
    TIME_FORMAT = "%Y-%m-%d %H:%M:%S"

    def __init__(self, subparser, script_name, version):
        """
        Creates a new Search sub-command instance.
        :param subparser: The subparser to use.  Generated from argparse.ArgumentParser.add_subparsers().
        :param script_name: The name of the script being run.
        :param version: The version of this software.
        """
        super().__init__(subparser, script_name)
        self._version = version

    def _setup_args(self, arg_parser):
        name = self._script_name
        epilog = ("Example:\n"
                  "\t" + name + " search --output-dir out *.fasta\n"
                                "\t\tSearches the files *.fasta for AMR genes using only the ResFinder database, storing results in the out/ directory.\n\n" +
                  "\t" + name + " search --pointfinder-organism salmonella --output-dir out *.fasta\n" +
                  "\t\tSearches *.fasta for AMR genes using ResFinder and PointFinder database with the passed organism, storing results in out/.")

        arg_parser = self._subparser.add_parser('search',
                                                epilog=epilog,
                                                formatter_class=argparse.RawTextHelpFormatter,
                                                help='Search for AMR genes')

        self._default_database_dir = AMRDatabasesManager.get_default_database_directory()
        cpu_count = multiprocessing.cpu_count()

        arg_parser.add_argument('-n', '--nprocs', action='store', dest='nprocs', type=int,
                                help='The number of processing cores to use [' + str(cpu_count) + '].',
                                default=cpu_count, required=False)
        arg_parser.add_argument('--pid-threshold', action='store', dest='pid_threshold', type=float,
                                help='The percent identity threshold [98.0].', default=98.0, required=False)
        arg_parser.add_argument('--percent-length-overlap-resfinder', action='store',
                                dest='plength_threshold_resfinder', type=float,
                                help='The percent length overlap for resfinder results [60.0].', default=60.0,
                                required=False)
        arg_parser.add_argument('--percent-length-overlap-pointfinder', action='store',
                                dest='plength_threshold_pointfinder', type=float,
                                help='The percent length overlap for pointfinder results [95.0].', default=95.0,
                                required=False)
        arg_parser.add_argument('--pointfinder-organism', action='store', dest='pointfinder_organism', type=str,
                                help='The organism to use for pointfinder {' + ', '.join(
                                    PointfinderBlastDatabase.get_available_organisms()) + '} [None].', default=None,
                                required=False)
        arg_parser.add_argument('--exclude-negatives', action='store_true', dest='exclude_negatives',
                                help='Exclude negative results (those sensitive to antimicrobials) [False].',
                                required=False)
        arg_parser.add_argument('--report-all-blast', action='store_true', dest='report_all_blast',
                                help='Report all blast hits (vs. only top blast hits) [False].',
                                required=False)
        arg_parser.add_argument('--exclude-resistance-phenotypes', action='store_true',
                                dest='exclude_resistance_phenotypes',
                                help='Exclude predicted antimicrobial resistances [False].',
                                required=False)
        arg_parser.add_argument('-d', '--database', action='store', dest='database', type=str,
                                help='The directory containing the resfinder/pointfinder databases [' + self._default_database_dir + '].',
                                default=self._default_database_dir, required=False)
        arg_parser.add_argument('-o', '--output-dir', action='store', dest='output_dir', type=str,
                                help="The output directory for results.  If unset prints all results to stdout.",
                                default=None, required=False)
        arg_parser.add_argument('--output-summary', action='store', dest='output_summary', type=str,
                                help="The name of the output file containing the summary results. Not be be used with '--output-dir'. [None]",
                                default=None, required=False)
        arg_parser.add_argument('--output-resfinder', action='store', dest='output_resfinder', type=str,
                                help="The name of the output file containing the resfinder results. Not be be used with '--output-dir'. [None]",
                                default=None, required=False)
        arg_parser.add_argument('--output-pointfinder', action='store', dest='output_pointfinder', type=str,
                                help="The name of the output file containing the pointfinder results. Not be be used with '--output-dir'. [None]",
                                default=None, required=False)
        arg_parser.add_argument('--output-settings', action='store', dest='output_settings', type=str,
                                help="The name of the output file containing the settings. Not be be used with '--output-dir'. [None]",
                                default=None, required=False)
        arg_parser.add_argument('--output-excel', action='store', dest='output_excel', type=str,
                                help="The name of the output file containing the excel results. Not be be used with '--output-dir'. [None]",
                                default=None, required=False)
        arg_parser.add_argument('files', nargs='+')

        return arg_parser

    def _print_dataframes_to_excel(self, outfile_path, summary_dataframe, resfinder_dataframe, pointfinder_dataframe,
                                   settings_dataframe):
        writer = pd.ExcelWriter(outfile_path, engine='xlsxwriter')

        sheetname_dataframe = {}
        sheetname_dataframe['Summary'] = summary_dataframe
        sheetname_dataframe['ResFinder'] = resfinder_dataframe
        if pointfinder_dataframe is not None:
            sheetname_dataframe['PointFinder'] = pointfinder_dataframe

        for name in ['Summary', 'ResFinder', 'PointFinder']:
            if name in sheetname_dataframe:
                sheetname_dataframe[name].to_excel(writer, name, freeze_panes=[1, 1], float_format="%0.2f", na_rep=self.BLANK)
        self._resize_columns(sheetname_dataframe, writer, max_width=50)

        settings_dataframe.to_excel(writer, 'Settings')
        self._resize_columns({'Settings': settings_dataframe}, writer, max_width=75, text_wrap=False)

        writer.save()

    def _resize_columns(self, sheetname_dataframe, writer, max_width, text_wrap = True):
        """
        Resizes columns in workbook.
        :param sheetname_dataframe: A map mapping the sheet name to a dataframe.
        :param writer: The ExcelWriter, which the worksheets already added using writer.to_excel
        :param max_width: The maximum width of the columns.
        :param text_wrap: Whether or not to turn on text wrapping if columns surpass max_width.
        :return: None
        """
        workbook = writer.book
        wrap_format = workbook.add_format({'text_wrap': text_wrap})
        for name in sheetname_dataframe:
            for i, width in enumerate(self._get_col_widths(sheetname_dataframe[name])):
                if width > max_width:
                    writer.sheets[name].set_column(i, i, width=max_width, cell_format=wrap_format)
                else:
                    writer.sheets[name].set_column(i, i, width=width)

    def _get_col_widths(self, df):
        """
        Calculate column widths based on column headers and contents
        :param df: The dataframe.
        :return: A generator giving the max width for each column.
        """
        idx_max = max([len(str(s)) for s in df.index.values] + [len(str(df.index.name))])
        yield idx_max

        extra = 2
        for c in df.columns:
            # get max length of column contents and length of column header (plus some extra)
            yield np.max([df[c].astype(str).str.len().max(), len(c)])+extra

    def _print_dataframe_to_text_file_handle(self, dataframe, file_handle):
        dataframe.to_csv(file_handle, sep="\t", float_format="%0.2f", na_rep=self.BLANK)

    def _print_settings_to_file(self, settings, file):
        file_handle = open(file, 'w')
        file_handle.write(get_string_with_spacing(settings))
        file_handle.close()

    def run(self, args):
        super(Search, self).run(args)

        start_time = datetime.datetime.now()

        if (len(args.files) == 0):
            raise CommandParseException("Must pass a fasta file to process", self._root_arg_parser, print_help=True)

        for file in args.files:
            if not path.exists(file):
                raise CommandParseException('File ['+file+'] does not exist', self._root_arg_parser)        

        if not path.isdir(args.database):
            if args.database == self._default_database_dir:
                raise CommandParseException(
                    "Default database does not exist. Perhaps try restoring with 'staramr db restore'",
                    self._root_arg_parser)
            else:
                raise CommandParseException(
                    "Database directory [" + args.database + "] does not exist. Perhaps try building with"+
                    "'staramr db build --dir " + args.database + "'",
                    self._root_arg_parser)

        if args.database == AMRDatabasesManager.get_default_database_directory():
            database_handler = AMRDatabasesManager.create_default_manager().get_database_handler()
            if database_handler.is_error():
                raise CommandParseException(
                    "Default database [" + database_handler.get_database_dir() + "] is in an error state. Please try " +
                    "restoring with 'staramr db restore'.",
                    self._root_arg_parser)
        else:
            database_handler = AMRDatabasesManager(args.database).get_database_handler()
            if database_handler.is_error():
                raise CommandParseException(
                    "Database [" + database_handler.get_database_dir() + "] is in an error state. Please try " +
                    "rebuilding the database with 'staramr db build --dir " + database_handler.get_database_dir() +"'.",
                    self._root_arg_parser)

        resfinder_database_dir = database_handler.get_resfinder_dir()
        pointfinder_database_dir = database_handler.get_pointfinder_dir()

        resfinder_database = ResfinderBlastDatabase(resfinder_database_dir)
        if (args.pointfinder_organism):
            if args.pointfinder_organism not in PointfinderBlastDatabase.get_available_organisms():
                raise CommandParseException("The only Pointfinder organism(s) currently supported are " + str(
                    PointfinderBlastDatabase.get_available_organisms()), self._root_arg_parser)
            pointfinder_database = PointfinderBlastDatabase(pointfinder_database_dir,
                                                            args.pointfinder_organism)
        else:
            pointfinder_database = None

        to_stdout = False
        hits_output_dir = None
        output_summary = None
        output_resfinder = None
        output_pointfinder = None
        output_excel = None
        output_settings = None
        if args.output_dir:
            if path.exists(args.output_dir):
                raise CommandParseException("Output directory [" + args.output_dir + "] already exists",
                                            self._root_arg_parser)
            else:
                hits_output_dir = path.join(args.output_dir, 'hits')
                mkdir(args.output_dir)
                mkdir(hits_output_dir)

                output_resfinder = path.join(args.output_dir, "resfinder.tsv")
                output_pointfinder = path.join(args.output_dir, "pointfinder.tsv")
                output_summary = path.join(args.output_dir, "summary.tsv")
                output_settings = path.join(args.output_dir, "settings.txt")
                output_excel = path.join(args.output_dir, 'results.xlsx')

                logger.info('--output-dir set. All files will be output to ['+args.output_dir+']')
        elif args.output_summary or args.output_resfinder or args.output_pointfinder or args.output_excel:
            logger.info('--output-dir not set. Files will be output to the respective --output-[type] setting')
            output_resfinder = args.output_resfinder
            output_pointfinder = args.output_pointfinder
            output_summary = args.output_summary
            output_settings = args.output_settings
            output_excel = args.output_excel
        else:
            logger.info('--output-dir and --output-[type] not set. Will print staramr summary to stdout')
            to_stdout = True

        with tempfile.TemporaryDirectory() as blast_out:
            blast_handler = BlastHandler(resfinder_database, args.nprocs, blast_out, pointfinder_database)

            amr_detection_factory = AMRDetectionFactory()
            amr_detection = amr_detection_factory.build(resfinder_database, blast_handler, pointfinder_database,
                                                        include_negatives=not args.exclude_negatives,
                                                        include_resistances=not args.exclude_resistance_phenotypes,
                                                        output_dir=hits_output_dir)
            amr_detection.run_amr_detection(args.files, args.pid_threshold, args.plength_threshold_resfinder,
                                            args.plength_threshold_pointfinder, args.report_all_blast)

            end_time = datetime.datetime.now()
            time_difference = end_time - start_time
            time_difference_minutes = "%0.2f" % (time_difference.total_seconds() / 60)

            logger.info("Finished. Took " + str(time_difference_minutes) + " minutes.")

            if to_stdout:
                self._print_dataframe_to_text_file_handle(amr_detection.get_summary_results(), sys.stdout)
            else:
                if output_resfinder:
                    logger.info('Writing resfinder to ['+output_resfinder+']')
                    with open(output_resfinder, 'w') as fh:
                        self._print_dataframe_to_text_file_handle(amr_detection.get_resfinder_results(), fh)
                else:
                    logger.info("--output-dir or --output-resfinder unset. No resfinder file will be written")

                if args.pointfinder_organism and output_pointfinder:
                    logger.info('Writing pointfinder to [' + output_pointfinder + ']')
                    with open(output_pointfinder, 'w') as fh:
                        self._print_dataframe_to_text_file_handle(amr_detection.get_pointfinder_results(), fh)
                else:
                    logger.info("--output-dir or --output-pointfinder unset. No pointfinder file will be written")

                if output_summary:
                    logger.info('Writing summary to [' + output_summary + ']')
                    with open(output_summary, 'w') as fh:
                        self._print_dataframe_to_text_file_handle(amr_detection.get_summary_results(), fh)
                else:
                    logger.info("--output-dir or --output-summary unset. No summary file will be written")

                if output_settings:
                    logger.info('Writing settings to [' + output_settings + ']')
                    settings = database_handler.info()
                    settings.insert(0, ['command_line', ' '.join(sys.argv)])
                    settings.insert(1, ['version', self._version])
                    settings.insert(2, ['start_time', start_time.strftime(self.TIME_FORMAT)])
                    settings.insert(3, ['end_time', end_time.strftime(self.TIME_FORMAT)])
                    settings.insert(4, ['total_minutes', time_difference_minutes])
                    if not args.exclude_resistance_phenotypes:
                        arg_drug_table = ARGDrugTable()
                        info = arg_drug_table.get_resistance_table_info()
                        settings.extend(info)
                        logger.info(
                            "Predicting AMR resistance phenotypes is enabled. The predictions are for microbiological resistance and *not* clinical resistance. This is an experimental feature which is continually being improved.")
                    self._print_settings_to_file(settings, output_settings)
                else:
                    logger.info("--output-dir or --output-settings unset. No settings file will be written")

                if output_excel:
                    logger.info('Writing Excel to [' + output_excel + ']')
                    settings_dataframe = pd.DataFrame(settings, columns=('Key', 'Value')).set_index('Key')

                    self._print_dataframes_to_excel(output_excel,
                                                    amr_detection.get_summary_results(),
                                                    amr_detection.get_resfinder_results(),
                                                    amr_detection.get_pointfinder_results(),
                                                    settings_dataframe)
                else:
                    logger.info("--output-dir or --output-excel unset. No Excel file will be written")

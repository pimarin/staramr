import abc
import logging
import os
from os import path

import Bio.SeqIO

from Bio.Alphabet import NucleotideAlphabet
from Bio.Seq import Seq
from Bio.SeqRecord import SeqRecord
from Bio.Blast import NCBIXML

from staramr.blast.results.BlastHitPartitions import BlastHitPartitions

logger = logging.getLogger('BlastResultsParser')

"""
Class for parsing BLAST results.
"""


class BlastResultsParser:

    def __init__(self, file_blast_map, blast_database, pid_threshold, plength_threshold, report_all=False, output_dir=None):
        """
        Creates a new class for parsing BLAST results.
        :param file_blast_map: A map/dictionary linking input files to BLAST results files.
        :param blast_database: The particular staramr.blast.AbstractBlastDatabase to use.
        :param pid_threshold: A percent identity threshold for BLAST results.
        :param plength_threshold: A percent length threshold for results.
        :param report_all: Whether or not to report all blast hits.
        :param output_dir: The directory where output files are being written.
        """
        __metaclass__ = abc.ABCMeta
        self._file_blast_map = file_blast_map
        self._blast_database = blast_database
        self._pid_threshold = pid_threshold
        self._plength_threshold = plength_threshold
        self._report_all = report_all
        self._output_dir = output_dir

    def parse_results(self):
        """
        Parses the BLAST files passed to this particular object.
        :return: A pandas.DataFrame containing the AMR matches from BLAST.
        """
        results = []

        for file in self._file_blast_map:
            databases = self._file_blast_map[file]
            out_file=self._get_out_file_name(file)
            hit_seq_records = []
            for database_name, blast_out in databases.items():
                logger.debug(str(blast_out))
                if (not os.path.exists(blast_out)):
                    raise Exception("Blast output [" + blast_out + "] does not exist")
                self._handle_blast_hit(file, database_name, blast_out, results, hit_seq_records)

            if hit_seq_records:
                logger.debug("Writting hits to "+out_file)
                Bio.SeqIO.write(hit_seq_records, out_file, 'fasta')
            else:
                logger.debug("No hits found, skipping writing output file to " + out_file)

        return self._create_data_frame(results)

    @abc.abstractmethod
    def _get_out_file_name(self, in_file):
        """
        Gets hits output file name given input file.
        :param in_file: The input file name.
        :return: The output file name.
        """
        pass

    def _handle_blast_hit(self, in_file, database_name, blast_file, results, hit_seq_records):
        blast_handle = open(blast_file)
        blast_records = NCBIXML.parse(blast_handle)
        for blast_record in blast_records:
            partitions = BlastHitPartitions()
            for alignment in blast_record.alignments:
                for hsp in alignment.hsps:
                    hit = self._create_hit(in_file, database_name, blast_record, alignment, hsp)
                    if hit.get_pid() > self._pid_threshold and hit.get_plength() > self._plength_threshold:
                        partitions.append(hit)
            for hits_non_overlapping in partitions.get_hits_nonoverlapping_regions():
                # sort by pid and then by plength
                hits_non_overlapping.sort(key=lambda x: (x.get_alignment_length(), x.get_pid(), x.get_plength()), reverse=True)
                if len(hits_non_overlapping) >= 1:
                    if self._report_all:
                        for hit in hits_non_overlapping:
                            self._append_results_to(hit, database_name, results, hit_seq_records)
                    else:
                        hit = hits_non_overlapping[0]
                        self._append_results_to(hit, database_name, results, hit_seq_records)
        blast_handle.close()

    @abc.abstractmethod
    def _create_data_frame(self, results):
        pass

    @abc.abstractmethod
    def _create_hit(self, file, database_name, blast_record, alignment, hsp):
        pass

    @abc.abstractmethod
    def _append_results_to(self, hit, database_name, results, hit_seq_records):
        seq_record = SeqRecord(Seq(hit.get_hsp_query_proper()), id=hit.get_hit_id(),
                         description='isolate: ' + hit.get_isolate_id() +
                                     ', contig: ' + hit.get_contig() +
                                     ', contig_start: ' + str(hit.get_contig_start()) +
                                     ', contig_end: ' + str(hit.get_contig_end()) +
                                     ', resistance_gene_start: ' + str(hit.get_resistance_gene_start()) +
                                     ', resistance_gene_end: ' + str(hit.get_resistance_gene_end()) +
                                     ', hsp/length: ' + str(hit.get_hsp_alignment_length())+'/'+str(hit.get_alignment_length()) +
                                     ', pid: ' + str("%0.2f%%" % hit.get_pid()) +
                                     ', plength: ' + str("%0.2f%%" % hit.get_plength()))
        logger.debug("seq_record="+repr(seq_record))
        hit_seq_records.append(seq_record)

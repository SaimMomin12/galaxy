#!/usr/bin/env python
#Dan Blankenberg
#For a set of intervals, this tool returns the same set of intervals 
#with 2 additional fields: the name of a Table/Feature and the number of
#bases covered. The original intervals are repeated for each Table/Feature.

import sys, struct, optparse, os, random
from galaxy import eggs
import pkg_resources; pkg_resources.require( "bx-python" )
import bx.intervals.io
import bx.bitset
try:
    import psyco
    psyco.full()
except:
    pass

assert sys.version_info[:2] >= ( 2, 4 )

class CachedRangesInFile:
    fmt = 'I'
    fmt_size = struct.calcsize( fmt )
    def __init__( self, filename ):
        self.file_size = os.stat( filename ).st_size
        self.file = open( filename, 'rb' )
        self.length = int( self.file_size / self.fmt_size / 2 )
        self._cached_ranges = [ None for i in xrange( self.length ) ]
    def __getitem__( self, i ):
        if self._cached_ranges[i] is not None:
            return self._cached_ranges[i]
        if i < 0: i = self.length + i
        offset = i * self.fmt_size * 2
        self.file.seek( offset )
        try:
            start = struct.unpack( self.fmt, self.file.read( self.fmt_size ) )[0]
            end = struct.unpack( self.fmt, self.file.read( self.fmt_size ) )[0]
        except Exception, e:
            raise IndexError, e
        self._cached_ranges[i] = ( start, end )
        return start, end
    def __len__( self ):
        return self.length

class RegionCoverage:
    def __init__( self, filename_base ):
        try:
            self._coverage = CachedRangesInFile( "%s.covered" % filename_base )
        except Exception, e:
            #print "Error loading coverage file %s: %s" % ( "%s.covered" % filename_base, e )
            self._coverage = []
        try: 
            self._total_coverage = int( open( "%s.total_coverage" % filename_base ).read() )
        except Exception, e:
            #print "Error loading total coverage file %s: %s" % ( "%s.total_coverage" % filename_base, e )
            self._total_coverage = 0
    def get_start_index( self, start ):
        #binary search: returns index of range closest to start
        if start > self._coverage[-1][1]:
            return len( self._coverage ) - 1
        i = 0
        j = len( self._coverage) - 1
        while i < j:
            k = ( i + j ) / 2
            if start <= self._coverage[k][1]:
                j = k
            else:
                i = k + 1
        return i
    def get_coverage( self, start, end ):
        return self.get_coverage_regions_overlap( start, end )[0]
    def get_coverage_regions_overlap( self, start, end ):
        return self.get_coverage_regions_index_overlap( start, end )[0:2]
    def get_coverage_regions_index_overlap( self, start, end ):
        if len( self._coverage ) < 1 or start > self._coverage[-1][1] or end < self._coverage[0][0]:
            return 0, 0, 0
        if self._total_coverage and start <= self._coverage[0][0] and end >= self._coverage[-1][1]:
            return self._total_coverage, len( self._coverage ), 0
        coverage = 0
        region_count = 0
        start_index = self.get_start_index( start )
        for i in xrange( start_index, len( self._coverage ) ):
            c_start, c_end = self._coverage[i]
            if c_start > end:
                break
            if c_start <= end and c_end >= start:
                coverage += min( end, c_end ) - max( start, c_start )
                region_count += 1
        return coverage, region_count, start_index

class CachedCoverageReader:
    def __init__( self, base_file_path, buffer = 10, table_names = None ):
        self._base_file_path = base_file_path
        self._buffer = buffer #number of chromosomes to keep in memory at a time
        self._coverage = {}
        if table_names is None: table_names = os.listdir( self._base_file_path )
        for tablename in table_names: self._coverage[tablename] = {}
    def iter_table_coverage_by_region( self, chrom, start, end ):
        for tablename, coverage, regions in self.iter_table_coverage_regions_by_region( chrom, start, end ):
            yield tablename, coverage
    def iter_table_coverage_regions_by_region( self, chrom, start, end ):
        for tablename, coverage, regions, index in self.iter_table_coverage_regions_index_by_region( chrom, start, end ):
            yield tablename, coverage, regions
    def iter_table_coverage_regions_index_by_region( self, chrom, start, end ):
        for tablename, chromosomes in self._coverage.iteritems():
            if chrom not in chromosomes:
                if len( chromosomes ) >= self._buffer:
                    #randomly remove one chromosome from this table
                    del chromosomes[ chromosomes.keys().pop( random.randint( 0, self._buffer - 1 ) ) ]
                chromosomes[chrom] = RegionCoverage( os.path.join ( self._base_file_path, tablename, chrom ) )
            coverage, regions, index = chromosomes[chrom].get_coverage_regions_index_overlap( start, end )
            yield tablename, coverage, regions, index

class TableCoverageSummary:
    def __init__( self, coverage_reader, chrom_lengths ):
        self.coverage_reader = coverage_reader
        self.chrom_lengths = chrom_lengths
        self.chromosome_coverage = {}
        self.total_interval_size = 0
        self.total_interval_count = 0
        self.table_coverage = {}
        self.table_chromosome_size = {}
        self.table_chromosome_count = {}
        self.table_regions_overlaped_count = {}
        self.interval_table_overlap_count = {}
        self._nr_region_size = None
    def add_region( self, chrom, start, end ):
        self.total_interval_size += ( end - start )
        self.total_interval_count += 1
        if chrom not in self.chromosome_coverage:
            self.chromosome_coverage[chrom] = bx.bitset.BitSet( self.chrom_lengths.get( chrom ) )
        self.chromosome_coverage[chrom].set_range( start, end - start )
        for table_name, coverage, regions in self.coverage_reader.iter_table_coverage_regions_by_region( chrom, start, end ):
            if table_name not in self.table_coverage:
                self.table_coverage[table_name] = 0
                self.table_chromosome_size[table_name] = {}
                self.table_regions_overlaped_count[table_name] = 0
                self.interval_table_overlap_count[table_name] = 0
                self.table_chromosome_count[table_name] = {}
            if chrom not in self.table_chromosome_size[table_name]:
                self.table_chromosome_size[table_name][chrom] = self.coverage_reader._coverage[table_name][chrom]._total_coverage
                self.table_chromosome_count[table_name][chrom] = len( self.coverage_reader._coverage[table_name][chrom]._coverage )
            self.table_coverage[table_name] += coverage
            if coverage:
                self.interval_table_overlap_count[table_name] += 1
            self.table_regions_overlaped_count[table_name] += regions
    def get_nr_coverage( self ):
        table_coverage = {}
        interval_table_overlap_count = {}
        table_regions_overlap_count = {}
        interval_count = 0
        region_start_end = {}
        for chrom, chromosome_bitset in self.chromosome_coverage.iteritems():
            end = 0
            last_end_index = {}
            while True:
                start = chromosome_bitset.next_set( end )
                if start >= chromosome_bitset.size: break
                end = chromosome_bitset.next_clear( start )
                interval_count += 1
                if chrom not in region_start_end:
                    region_start_end[chrom] = [start, end]
                else:
                    if start < region_start_end[chrom][0]: region_start_end[chrom][0] = start
                    if end > region_start_end[chrom][1]: region_start_end[chrom][1] = end
                for table_name, coverage, region_count, start_index in self.coverage_reader.iter_table_coverage_regions_index_by_region( chrom, start, end ):
                    if table_name not in table_coverage:
                        table_coverage[table_name] = 0
                        interval_table_overlap_count[table_name] = 0
                        table_regions_overlap_count[table_name] = 0
                    table_coverage[table_name] += coverage
                    if coverage:
                        interval_table_overlap_count[table_name] += 1
                        table_regions_overlap_count[table_name] += region_count
                        if table_name in last_end_index and last_end_index[table_name] == start_index:
                            table_regions_overlap_count[table_name] -= 1
                        last_end_index[table_name] = start_index + region_count - 1
        table_region_coverage = {}
        table_region_count = {}
        for chrom, start_end in region_start_end.items():
            for table_name, coverage, region_count in self.coverage_reader.iter_table_coverage_regions_by_region( chrom, start_end[0], start_end[1] ):
                if table_name not in table_region_coverage:
                    table_region_coverage[table_name] = 0
                    table_region_count[table_name] = 0
                table_region_coverage[table_name] += coverage
                table_region_count[table_name] += region_count
        return table_region_coverage, table_region_count, interval_count, table_coverage, table_regions_overlap_count, interval_table_overlap_count
    def get_nr_region_size( self ):
        if self._nr_region_size is None:
            self._nr_region_size = 0
            for chrom, chromosome_bitset in self.chromosome_coverage.iteritems():
                self._nr_region_size += chromosome_bitset.count_range()
        return self._nr_region_size
    def iter_table_coverage( self ):
        table_region_coverage, table_region_count, nr_interval_count, nr_table_coverage, nr_table_regions_overlap_count, nr_interval_table_overlap_count = self.get_nr_coverage()
        for table_name in self.table_coverage:
            #TODO: determine a type of statistic, then calculate and report here
            yield table_name, sum( self.table_chromosome_size.get( table_name, [] ).values() ), sum( self.table_chromosome_count.get( table_name, [] ).values() ), table_region_coverage.get( table_name, 0 ), table_region_count.get( table_name, 0 ), self.total_interval_count, self.total_interval_size,  self.table_coverage[table_name], self.table_regions_overlaped_count.get( table_name, 0), self.interval_table_overlap_count.get( table_name, 0 ), nr_interval_count, self.get_nr_region_size(), nr_table_coverage[table_name], nr_table_regions_overlap_count.get( table_name, 0 ), nr_interval_table_overlap_count.get( table_name, 0 )

def profile_per_interval( interval_filename, chrom_col, start_col, end_col, out_filename, keep_empty, coverage_reader ):
    out = open( out_filename, 'wb' )
    for region in bx.intervals.io.NiceReaderWrapper( open( interval_filename, 'rb' ), chrom_col = chrom_col, start_col = start_col, end_col = end_col, fix_strand = True, return_header = False, return_comments = False ):
        for table_name, coverage, region_count in coverage_reader.iter_table_coverage_regions_by_region( region.chrom, region.start, region.end ):
            if keep_empty or coverage:
                #only output regions that have atleast 1 base covered unless empty are requested
                out.write( "%s\t%s\t%s\t%s\n" % ( "\t".join( region.fields ), table_name, coverage, region_count ) )
    out.close()

def profile_summary( interval_filename, chrom_col, start_col, end_col, out_filename, keep_empty, coverage_reader, chrom_lengths ):
    out = open( out_filename, 'wb' )
    table_coverage_summary = TableCoverageSummary( coverage_reader, chrom_lengths )
    for region in bx.intervals.io.NiceReaderWrapper( open( interval_filename, 'rb' ), chrom_col = chrom_col, start_col = start_col, end_col = end_col, fix_strand = True, return_header = False, return_comments = False ):
        table_coverage_summary.add_region( region.chrom, region.start, region.end )
    
    out.write( "#tableName\ttableChromosomeCoverage\ttableChromosomeCount\ttableRegionCoverage\ttableRegionCount\tallIntervalCount\tallIntervalSize\tallCoverage\tallTableRegionsOverlaped\tallIntervalsOverlapingTable\tnrIntervalCount\tnrIntervalSize\tnrCoverage\tnrTableRegionsOverlaped\tnrIntervalsOverlapingTable\n" )
    for table_name, table_chromosome_size, table_chromosome_count, table_region_coverage, table_region_count, total_interval_count, total_interval_size, total_coverage, table_regions_overlaped_count, interval_region_overlap_count, nr_interval_count, nr_region_size, nr_coverage, nr_table_regions_overlaped_count, nr_interval_table_overlap_count in table_coverage_summary.iter_table_coverage():
        if keep_empty or total_coverage:
            #only output tables that have atleast 1 base covered unless empty are requested
            out.write( "%s\t%s\t%s\t%s\t%s\t%s\t%s\t%s\t%s\t%s\t%s\t%s\t%s\t%s\t%s\n" % ( table_name, table_chromosome_size, table_chromosome_count, table_region_coverage, table_region_count, total_interval_count, total_interval_size, total_coverage, table_regions_overlaped_count, interval_region_overlap_count, nr_interval_count, nr_region_size, nr_coverage, nr_table_regions_overlaped_count, nr_interval_table_overlap_count ) )
    out.close()

class ChromosomeLengths:
    def __init__( self, filename ):
        self.chroms = {}
        try:
            for line in open( filename ):
                try:
                    fields = line.strip().split( "\t" )
                    self.chroms[fields[0]] = int( fields[1] )
                except:
                    continue
        except:
            pass
    def get( self, name ):
        return self.chroms.get( name, bx.bitset.MAX )

def __main__():
    parser = optparse.OptionParser()
    parser.add_option(
        '-k','--keep_empty',
        action="store_true",
        dest='keep_empty',
        default=False,
        help='Keep tables with 0 coverage'
    )
    parser.add_option(
        '-b','--buffer',
        dest='buffer',
        type='int',default=10,
        help='Number of Chromosomes to keep buffered'
    )
    parser.add_option(
        '-c','--chrom_col',
        dest='chrom_col',
        type='int',default=1,
        help='Chromosome column'
    )
    parser.add_option(
        '-s','--start_col',
        dest='start_col',
        type='int',default=2,
        help='Start Column'
    )
    parser.add_option(
        '-e','--end_col',
        dest='end_col',
        type='int',default=3,
        help='End Column'
    )
    parser.add_option(
        '-p','--path',
        dest='path',
        type='str',default='/depot/data2/galaxy/annotation_profiler/hg18',
        help='Path to profiled data for this organism'
    )
    parser.add_option(
        '-l','--lengths',
        dest='lengths',
        type='str',default='test-data/shared/ucsc/hg18.len',
        help='Path to chromosome lengths data for this organism'
    )
    parser.add_option(
        '-t','--table_names',
        dest='table_names',
        type='str',default='None',
        help='Path to profiled data for this organism'
    )
    parser.add_option(
        '-i','--input',
        dest='interval_filename',
        type='str',
        help='Input Interval File'
    )
    parser.add_option(
        '-o','--output',
        dest='out_filename',
        type='str',
        help='Input Interval File'
    )
    parser.add_option(
        '-S','--summary',
        action="store_true",
        dest='summary',
        default=False,
        help='Display Summary Results'
    )
    
    options, args = parser.parse_args()
    
    table_names = options.table_names.split( "," )
    if table_names == ['None']: table_names = None
    coverage_reader = CachedCoverageReader( options.path, buffer = options.buffer, table_names = table_names )
    
    if options.summary:
        profile_summary( options.interval_filename, options.chrom_col - 1, options.start_col - 1, options.end_col -1, options.out_filename, options.keep_empty, coverage_reader, ChromosomeLengths( options.lengths ) )
    else:
        profile_per_interval( options.interval_filename, options.chrom_col - 1, options.start_col - 1, options.end_col -1, options.out_filename, options.keep_empty, coverage_reader )
    

if __name__ == "__main__": __main__()

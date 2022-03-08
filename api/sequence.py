import os
import hashlib
import json
import yaml
import xmltodict
import logging
from subprocess import Popen, PIPE
from xml.parsers.expat import ExpatError


def log_results(results: list):
    """
    Log command results
    :param results:
    :return:
    """
    for result in results:
        for k, v in result.items():
            if isinstance(v, (bytes, bytearray)):
                v = v.decode('utf-8')
            msg = {k: v}
            if k == 'stdout' and v != '':
                logging.info(msg)
            if k == 'stderr' and v != '':
                logging.error(msg)


def execute(commands: list) -> list:
    """
    Function that executes a series of command strings, and raises exception on failure
    :param commands: list of command strings
    """
    results = []
    for command in commands:
        p = Popen(command, shell=True, stdin=PIPE, stdout=PIPE, stderr=PIPE, close_fds=True)
        stdout, stderr = p.communicate()
        if p.returncode == 1:
            raise SystemExit()
        result = {'command': command, 'stdout': stdout, 'stderr': stderr}
        results.append(result)
    return results


def get_id(body: dict) -> str:
    """
    Generate Id from hash of body
    :param body:
    :return:
    """
    body_str = json.dumps(body)
    return hashlib.md5(body_str.encode('utf-8')).hexdigest()


def load_xml(file: str) -> dict:
    """
    Return dictionary from XML file
    :param file:
    :return:
    """

    with open(file) as f:
        data = xmltodict.parse(f.read())
        f.close()

    return data


def load_json(file: str) -> dict:
    """
    Return dictionary from JSON file
    :param file:
    :return:
    """

    with open(file) as f:
        data = json.load(f)
        f.close()

    return data


def write_json(data: dict, file: str):
    """
    Write JSON file from dict
    :param data:
    :param file:
    :return:
    """

    with open(file, 'w') as f:
        json.dump(data, f)


def write_file(file: str, lines: list):
    """
    Write file from list of lines
    :param file:
    :param lines:
    :return:
    """

    f = open(file, "w")  # append mode
    f.writelines(lines)
    f.close()


def load_accessions(blast_results: dict) -> list:
    """
    Return list of accession dictionaries from blastn results
    :param blast_results:
    :return:
    """
    report = blast_results['BlastOutput2'][0]['report']
    hits = report['results']['search']['hits']

    # build accessions from hits
    accessions = []
    for hit in hits:
        accession_number = hit['description'][0]['accession']
        accession_ids = str(hit['description'][0]['id']).split('|')
        accession_id = accession_number
        for element in accession_ids:
            if accession_number in element:
                accession_id = element

        description = hit['description'][0]['title']
        hit_sequence = hit['hsps'][0]['hseq']
        accession = {'id': accession_id, 'description': description, 'sequence': hit_sequence}
        logging.debug(json.dumps(accession))
        accessions.append(accession)

    return accessions


def fetch_accession(xml_file: str, accession_id: str) -> int:
    try:
        efetch_command = f'/usr/local/bin/efetch -db nuccore -id {accession_id} -format gb -mode xml > {xml_file}'
        command_results = execute([efetch_command])
        log_results(command_results)
        resp_code = 0
    except SystemExit as e:
        logging.warning(str(e))
        resp_code = 1

    return resp_code


def clean_fasta(fasta_sequence: str, remove_gaps: bool = True) -> dict:
    """
    Clean FASTA sequence
    :param fasta_sequence:
    :param remove_gaps:
    :return:
    """

    lines = fasta_sequence.splitlines()
    description = ''
    seq = []
    for line in lines:
        if '>' in line:
            description = line
        else:
            seq.append(line)

    sequence = ''.join(seq)

    gap_chars = ['-', '.']

    if remove_gaps:
        for gap_char in gap_chars:
            logging.debug(f'removing gap char: "{gap_char}", from: {sequence}')
            sequence = sequence.replace(gap_char, '')
            logging.debug(f'clean_sequence: {sequence}')

    fasta = {'description': description, 'sequence': sequence}

    return fasta


def load_countries() -> dict:
    """
    Loads counties.yaml as dictionary
    :return:
    """

    countries = {}
    this_dir = os.path.dirname(os.path.realpath(__file__))
    countries_file = f'{this_dir}/countries.yaml'

    with open(countries_file, "r") as stream:
        try:
            countries = yaml.safe_load(stream)
        except yaml.YAMLError as e:
            logging.error(e)

    logging.debug(json.dumps(countries))

    return countries


def country_search(countries: dict = None, location_data: str = None) -> str:
    """
    Approximates country of origin
    :param countries:
    :param location_data:
    :return:
    """

    country = ''
    approx_char = '*'

    if countries:

        for key, value in countries.items():
            if key in location_data:
                country = key
                break

        if country == '':
            for key, value in countries.items():
                alts = value.get('alts', None)

                if alts:
                    for alt in alts:
                        if alt in location_data:
                            country = key
                            break
                    if country != '':
                        break

        if country == '':
            for key, value in countries.items():
                alpha_3 = value.get('alpha_3')

                if f' {alpha_3} ' in location_data or f'{alpha_3}:' in location_data:
                    country = f'{approx_char}{key}'
                    break

    if country == '':
        logging.warning(f'NO COUNTRY DATA FOUND: {location_data}')
    else:
        country = f'{approx_char}{country}'

    return country


def load_fasta(
        id: str,
        accessions: list,
        add_location: bool = True,
        remove_gaps: bool = True,
        include_accession: bool = False) -> list:
    """
    Return list of fasta dictionaries from accessions
    :param id:
    :param accessions: list of accession ids
    :param add_location:
    :param remove_gaps:
    :param include_accession:
    :return:
    """

    fastas = []

    # pre-load countries dictionary
    countries = {}
    if add_location:
        countries = load_countries()

    # run efetch to pull all accession details
    for accession in accessions:
        accession_id = accession['id']

        xml_file = f'/blast/fasta/{id}.{accession_id}.xml'
        json_file = f'/blast/fasta/{id}.{accession_id}.json'

        skip = False
        # fetch accession from Genbank
        if fetch_accession(xml_file, accession_id) > 0:
            skip = True

        # load results to a_object
        a_object = {}
        if not skip:
            try:
                a_object = load_xml(xml_file)
                write_json(a_object, json_file)
            except ExpatError as e:
                logging.warning(str(e))
                skip = True

        # create fasta dictionary
        if not skip:
            try:
                organism = a_object['GBSet']['GBSeq']['GBSeq_organism']
                description = f'{accession_id} {organism}'

                if add_location:
                    location = None
                    qualifiers = a_object['GBSet']['GBSeq']['GBSeq_feature-table']['GBFeature'][0]['GBFeature_quals']['GBQualifier']
                    for qualifier in qualifiers:
                        if 'country' in qualifier.get('GBQualifier_name'):
                            location = qualifier.get('GBQualifier_value')
                            if location != '' and location is not None:
                                break

                    if location is None or location == '':
                        location = country_search(countries=countries, location_data=json.dumps(a_object))

                    if location:
                        a_object['location'] = location
                        description += f' {location}'

                # sequence = accession['sequence']
                sequence = a_object['GBSet']['GBSeq']['GBSeq_sequence']

                # remove gaps, conditionally
                sequence = clean_fasta(sequence, remove_gaps=remove_gaps).get('sequence')

                # add fasta to results
                if include_accession:
                    fasta = {'description': description, 'sequence': sequence, 'accession': a_object}
                else:
                    fasta = {'description': description, 'sequence': sequence}

                fastas.append(fasta)

            except KeyError as e:
                logging.error(f'{e}')

        # clean up files
        for file in [xml_file, json_file]:
            if os.path.exists(file):
                os.remove(file)
            else:
                logging.warning(f'file does not exist: {file}')

    return fastas


def mycolab_stamp(description: str, mycolab_id: str = None) -> str:
    """
    Prefix FASTA description with MycoLab ID
    :param description:
    :param mycolab_id:
    :return:
    """

    mycolab_name = 'MycoLab'
    if mycolab_id:
        # truncate Id to 10 chars
        mycolab_name = f'{mycolab_name}-{mycolab_id[0:10]}'

    # prep by removing '>'
    description = description.replace('>', '')

    # prefix with MycoLab stamp
    if len(description) > 0:
        description = f'{mycolab_name} {description}'
    else:
        description = f'{mycolab_name}'

    return description


def query(body: dict = None, **kwargs):
    """
    Query Genbank for matching sequences
    :param body: query options
      Example:
      {
        "location": true,
        "match": 98.5,
        "results": 100,
        "sequence": "ACTAtGttGCCTtGGCAGGCTGGCAGCAGCCTGCCGGTGGACCTCAACTCTTGAATCTCTG..."
      }
    :param kwargs:
    :return: list of fasta sequence dicts
      Example:
      [
        {
          "description": "MK373018.1 Cudonia confusa USA",
          "sequence": "ACTATGTTGCCTTGGCAGGCTGGCAGCAGCCTGCCGGTGGACCTCAACTCTTGAATCTCT..."
        }
      ]
    """
    logging.debug(f'kwargs: {kwargs}')

    # generate Id from body
    id = get_id(body)

    # location enrichment
    add_location = body.get('location', True)

    # alignment gap removal
    remove_gaps = body.get('clean', True)

    # include accession in FASTA object
    include_accession = body.get('accession', False)

    # add mycolab stamp
    add_stamp = body.get('stamp', True)

    # maximum results
    max_results = body.get('results', 50)

    # minimum identity match
    min_match = body.get('match', 90)

    # clean fasta query
    query_fasta = clean_fasta(body.get('sequence'), remove_gaps=remove_gaps)
    query_description = query_fasta.get('description')
    query_sequence = query_fasta.get('sequence')
    logging.debug(f'"query_description": "{query_description}", "query_sequence": {query_sequence}')

    # write fas query
    q_file = f'/blast/queries/{id}.fas'
    write_file(q_file, [query_sequence])

    # set blastn options
    word_size = 28
    output_format = 15  # JSON
    database = 'nt'     # nucleotide
    o_file = f'/blast/fasta/{id}.json'
    option_list = [
        '-remote',
        f'-db {database}',
        f'-word_size {word_size}',
        f'-outfmt {output_format}',
        f'-perc_identity {min_match}',
        f'-max_target_seqs {max_results}',
        f'-query {q_file}',
        f'-out {o_file}'
    ]
    options = ' '.join(option_list)

    # execute blast query
    blastn_command = f'/usr/local/bin/blastn {options}'
    logging.debug(blastn_command)
    command_results = execute([blastn_command])
    log_results(command_results)

    # load accessions from blast output
    blast_results = load_json(o_file)
    accessions = load_accessions(blast_results)

    # load fasta from accessions
    resp = load_fasta(id, accessions, add_location=add_location, remove_gaps=remove_gaps, include_accession=include_accession)

    # conditionally add MycoLab stamp
    if add_stamp:
        description = mycolab_stamp(query_description, mycolab_id=id)
    else:
        description = query_description

    # insert query fasta as first record
    mycolab_fasta = {'description': description, 'sequence': query_sequence}
    resp.insert(0, mycolab_fasta)

    # write original query to disk
    write_json(body, f'/blast/fasta/mycolab-query-{id}.json')

    # clean up temp files
    for file in [q_file, o_file]:
        if os.path.exists(file):
            os.remove(file)
        else:
            logging.warning(f'file does not exist: {file}')

    return resp, 200


# todo: add _local_ genbank API sequence methods
# sequence API method stubs
def post(body: dict = None, **kwargs):
    logging.debug(f'kwargs: {kwargs}')
    id = get_id(body)
    resp = {
        'id': id
    }
    return resp, 200


def put(id: str = None, body: dict = None, **kwargs):
    params = {
        'id': id,
        'body': body,
        'kwargs': kwargs
    }
    logging.debug(f'{params}')
    pass


def get(id: str = None, **kwargs):
    params = {
        'id': id,
        'kwargs': kwargs
    }
    logging.debug(f'{params}')
    pass


def delete(id: str = None, **kwargs):
    params = {
        'id': id,
        'kwargs': kwargs
    }
    logging.debug(f'{params}')
    pass

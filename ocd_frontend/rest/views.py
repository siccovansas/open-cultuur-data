from flask import Blueprint, current_app, request, jsonify

from ocd_frontend.rest import OcdApiError, decode_json_post_data

bp = Blueprint('api', __name__)

def parse_search_request(data):
    # Return an error when no query or an empty query string is provied
    query = data.get('query', None)
    if not query:
        raise OcdApiError('Missing \'query\'', 400)

    # Check if 'size' was specified, if not, fallback to default
    try:
        n_size = int(data.get('size', current_app.config['DEFAULT_SEARCH_SIZE']))
    except ValueError:
        raise OcdApiError('Invalid value for \'size\'', 400)
    if n_size < 0 or n_size > current_app.config['MAX_SEARCH_SIZE']:
        raise OcdApiError('Value of \'size\' must be between 0 and %s' %
            current_app.config['MAX_SEARCH_SIZE'], 400)

    # Check if 'from' was specified, if not, fallback to zero
    try:
        n_from = int(data.get('from', 0))
    except ValueError:
        raise OcdApiError('Invalid value for \'from\'', 400)
    if n_from < 0:
        raise OcdApiError('Value of \'from\' must 0 or larger', 400)

    # Check if 'sort' was specified, if not, fallback to '_score'
    sort = data.get('sort', '_score')
    if sort not in current_app.config['SORTABLE_FIELDS']:
        raise OcdApiError('Invalid value for \'sort\', sortable fields are: %s'
                          % ', '.join(current_app.config['SORTABLE_FIELDS']), 400)

    # Check if 'order' was specified, if not, fallback to desc
    order = data.get('order', 'desc')
    if order not in ['asc', 'desc']:
        raise OcdApiError('Invalid value for \'order\', must be asc or desc', 400)

    # Check which 'facets' are requested
    req_facets = data.get('facets', {})
    if type(req_facets) is not dict:
        raise OcdApiError('\'facets\' should be an object', 400)

    facets = {}
    available_facets = current_app.config['AVAILABLE_FACETS']

    # Inspect all requested facets and override the default settings
    # where necessary
    for facet, facet_opts in req_facets.iteritems():
        if facet not in available_facets:
            raise OcdApiError('\'%s\' is not a valid facet' % facet, 400)

        # Take the default facet options from the settings
        facets[facet] = available_facets[facet]

        f_type =  facets[facet].keys()[0]
        if f_type == 'terms':
            if 'size' in facet_opts:
                size = facet_opts['size']
                if type(size) is not int:
                    raise OcdApiError('\'facets.%s.size\' should be an integer' % facet, 400)

                facets[facet][f_type]['size'] = size

        elif f_type == 'date_histogram':
            if 'interval' in facet_opts:
                interval = facet_opts['interval']
                if type(interval) is not unicode:
                    raise OcdApiError('\'facets.%s.interval\' should be a strimg' % facet, 400)

                if interval not in current_app.config['ALLOWED_DATE_INTERVALS']:
                    raise OcdApiError('\'%s\' is an invalid interval for '
                                         '\'facets.%s.interval\'' % (interval, facet), 400)

                facets[facet][f_type]['interval'] = interval

    # Check which 'filters' are requested
    requested_filters = data.get('filters', {})
    if type(requested_filters) is not dict:
        raise OcdApiError('\'filters\' should be an object', 400)

    filters = []
    # Inspect all requested filters and add them to the list of filters
    for r_filter, filter_opts in requested_filters.iteritems():
        # Use the facet defenitions to check if the requested filter can be used
        if r_filter not in available_facets:
            raise OcdApiError('\'%s\' is not a valid filter' % r_filter, 400)

        f_type =  available_facets[r_filter].keys()[0]
        if f_type == 'terms':
            if 'terms' not in filter_opts:
                raise OcdApiError('Missing \'filters.%s.terms\'' % r_filter, 400)

            if type(filter_opts['terms']) is not list:
                raise OcdApiError('\'filters.%s.terms\' should be an array' % r_filter, 400)

            # Check the type of each item in the list
            for term in filter_opts['terms']:
                if type(term) is not unicode and type(term) is not int:
                    raise OcdApiError('\'filters.%s.terms\' should only contain strings and integers' % r_filter, 400)

            filters.append({
                'terms': {
                    available_facets[r_filter]['terms']['field']: filter_opts['terms']
                }
            })
        elif f_type == 'date_histogram':
            if type(filter_opts) is not dict:
                raise OcdApiError('\'filters.%s\' should be an object' % r_filter, 400)

            field = available_facets[r_filter]['date_histogram']['field']
            r_filter =  {'range': {field: {}}}

            if 'from' in filter_opts:
                r_filter['range'][field]['from'] = filter_opts['from']

            if 'to' in filter_opts:
                r_filter['range'][field]['to'] = filter_opts['to']

            filters.append(r_filter)

    return {
        'query': query,
        'n_size': n_size,
        'n_from': n_from,
        'sort': sort,
        'order': order,
        'facets': facets,
        'filters': filters
    }


def format_search_results(results):
    del results['_shards']
    del results['timed_out']

    for hit in results['hits']['hits']:
        del hit['_index']
        del hit['_type']

    return results


@bp.route('/search', methods=['POST'])
@decode_json_post_data
def search():
    search_req = parse_search_request(request.data)

    # Construct the query we are going to send to Elasticsearch

    es_q = {
        'query': {
            'filtered': {
                'query': {
                    'simple_query_string': {
                        'query': search_req['query'],
                        'default_operator': 'OR',
                        'fields': [
                            'title^3',
                            'authors^2',
                            'description^2',
                            'meta.original_object_id',
                            'all_text'
                        ]
                    }
                },
                'filter': {}
            }
        },
        'facets': search_req['facets'],
        'size': search_req['n_size'],
        'from': search_req['n_from'],
        'sort': {
            search_req['sort']: {'order': search_req['order']}
        },
        '_source': {
            'exclude': ['all_text']
        }
    }

    if search_req['filters']:
        es_q['query']['filtered']['filter'] = {
            'bool': { 'must': search_req['filters']}
        }

    es_r = current_app.es.search(body=es_q, index=current_app.config['COMBINED_INDEX'])

    return jsonify(format_search_results(es_r))

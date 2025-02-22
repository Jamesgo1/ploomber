import os
import contextlib
import tempfile
from pathlib import Path
from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor, as_completed

from ploomber.exceptions import DAGWithDuplicatedProducts
from ploomber.products.metaproduct import MetaProduct
from ploomber.products import File
from ploomber.io import pretty_print


def _generate_error_message_pair(key, value):
    return f'* {key!r} generated by tasks: {pretty_print.iterable(value)}'


def _generate_error_message(duplicated):
    return '\n'.join(
        _generate_error_message_pair(key, value)
        for key, value in duplicated.items())


def check_duplicated_products(dag):
    """
    Raises an error if more than one task produces the same product.

    Note that this relies on the __hash__ and __eq__ implementations of
    each Product to determine whether they're the same or not. This
    implies that a relative File and absolute File pointing to the same file
    are considered duplicates and SQLRelations (in any of its flavors) are
    the same when they resolve to the same (schema, name, type) tuple
    (i.e., client is ignored), this because when using the generic SQLite
    backend for storing SQL product metadata, the table only relies on schema
    and name to retrieve metadata.
    """
    prod2tasknames = defaultdict(lambda: [])

    for name in dag._iter():
        product = dag[name].product

        if isinstance(product, MetaProduct):
            for p in product.products:
                prod2tasknames[p].append(name)
        else:
            prod2tasknames[product].append(name)

    duplicated = {k: v for k, v in prod2tasknames.items() if len(v) > 1}

    if duplicated:
        raise DAGWithDuplicatedProducts(
            'Tasks must generate unique products. '
            'The following products appear in more than '
            f'one task:\n{_generate_error_message(duplicated)}')


def flatten_products(elements, require_file_client=True):
    flat = []

    for prod in elements:
        if isinstance(prod, MetaProduct):
            flat.extend([p for p in prod if isinstance(p, File) and p.client])
        elif (isinstance(prod, File) and prod.client
              and require_file_client) or (isinstance(prod, File)
                                           and not require_file_client):
            flat.append(prod)

    return flat


def fetch_remote_metadata_in_parallel(dag):
    """Fetches remote metadta in parallel from a list of Files
    """
    files = flatten_products(dag[t].product for t in dag._iter()
                             if isinstance(dag[t].product, File)
                             or isinstance(dag[t].product, MetaProduct))

    if files:
        with ThreadPoolExecutor(max_workers=64) as executor:
            future2file = {
                executor.submit(file._remote._fetch_remote_metadata): file
                for file in files
            }

            for future in as_completed(future2file):
                exception = future.exception()

                if exception:
                    local = future2file[future]
                    raise RuntimeError(
                        'An error occurred when fetching '
                        f'remote metadata for file {local!r}') from exception


@contextlib.contextmanager
def _path_for_plot(path_to_plot, fmt):
    """Context manager to manage DAG.plot

    Parameters
    ----------
    path_to_plot : str
        Where to store the plot. If 'embed', It returns a temporary empty file
        otherwise and deletes it when exiting. Otherwise, it just passes the
        value
    """
    if path_to_plot == 'embed':
        fd, path = tempfile.mkstemp(suffix=f'.{fmt}')
        os.close(fd)
    else:
        path = str(path_to_plot)
    try:
        yield path
    finally:
        if path_to_plot == 'embed' and fmt != 'html':
            Path(path).unlink()


def iter_file_products(dag):
    for t in dag._iter():
        product = dag[t].product

        if isinstance(product, File):
            yield product
        elif isinstance(product, MetaProduct):
            for prod in product:
                if isinstance(prod, File):
                    yield prod


def _get_parent_from_product(product, base_dir):
    path = Path(str(product._identifier))
    current = Path(base_dir or '').resolve()

    if path.is_absolute():
        try:
            # products loaded from pipeline.yaml are always absolute, so try
            # to see if they're relative to the current directory
            path = path.relative_to(current)
        except ValueError as e:
            raise ValueError('Absolute product paths '
                             f'are not supported: {str(path)!r}') from e

    return str(path.parent)


def extract_product_prefixes(dag, base_dir=None):
    files = set(
        _get_parent_from_product(p, base_dir) for p in iter_file_products(dag))

    return sorted(files)

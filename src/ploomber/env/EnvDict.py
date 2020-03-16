import importlib
from itertools import chain
from glob import iglob
import platform
from pathlib import Path
from collections.abc import Mapping

import yaml

from ploomber.env import validate
from ploomber.env.expand import EnvironmentExpander
from ploomber.env.FrozenJSON import FrozenJSON


# TODO: custom expanders, this could be done trough another special directive
# such as _expander_class to know which class to use
class EnvDict(Mapping):
    """
    Implements the initialization functionality for Env, except it allows
    to more than one instance to exist, this is used internally to allow
    factory functions introspection without having to create an actual Env

    Parameters
    ----------
    source : dict or str
        If str, it will be interpreted as a path to a YAML file

    """

    def __init__(self, source):

        # load data
        (self._raw_data,
         # these two will be None if source is a dict
         self.path_to_env,
         self.name) = load_from_source(source)

        # check raw data is ok
        validate.raw_data_keys(self._raw_data)

        # expand _module special key, return its expanded value
        self.preprocessed = raw_preprocess(self._raw_data,
                                           self.path_to_env)

        # initialize expander, which converts placeholders to their values
        # we need to pass path_to_env since the {{here}} placeholder resolves
        # to its parent
        self.expander = EnvironmentExpander(self.preprocessed,
                                            self.path_to_env)

        # now expand all values
        self._data = self.expander.expand_raw_dictionary(self._raw_data)

    def __getattr__(self, key):
        return self[key]

    def __getitem__(self, key):
        if key in self.preprocessed:
            return FrozenJSON(self.preprocessed[key])
        else:
            return FrozenJSON(self._data[key])

    def __iter__(self):
        for k in self._data:
            yield k

    def __len__(self):
        return len(self._data)

    def __str__(self):
        return str(self._data)

    def __repr__(self):
        return '{}({})'.format(type(self).__name__, str(self))

    def _replace_value(self, value, keys_all):
        keys_to_final_dict = keys_all[:-1]
        key_to_edit = keys_all[-1]

        dict_to_edit = self._data

        for e in keys_to_final_dict:
            dict_to_edit = dict_to_edit[e]

        if dict_to_edit.get(key_to_edit) is None:
            dotted_path = '.'.join(keys_all)
            raise KeyError('Trying to replace key "{}" in env, '
                           'but it does not exist'
                           .format(dotted_path))

        dict_to_edit[key_to_edit] = (self.expander
                                     .expand_raw_value(value,
                                                       keys_all))


def load_from_source(source):
    """
    Loads from a dictionary or a YAML and applies preprocesssing to the
    dictionary

    Returns
    -------
    dict
        Raw dictioanry
    pathlib.Path
        Path to the loaded file, None if source is a dict
    str
        Name, if loaded from a YAML file with the env.{name}.yaml format,
        None if another format or if source is a dict
    """
    if isinstance(source, Mapping):
        # dictiionary, path, name
        return source, None, None

    elif source is None:
        # look for an env.{name}.yaml, if that fails, try env.yaml
        name = platform.node()
        path_found = find_env_w_name(name)

        if path_found is None:
            raise FileNotFoundError('Tried to initialize environment with '
                                    'None, but automatic '
                                    'file search failed to locate '
                                    'env.{}.yaml nor env.yaml in the '
                                    'current directory nor 6 levels up'
                                    .format(name))
        else:
            source = path_found

    elif isinstance(source, (str, Path)):
        source_found = find_env(source)

        if source_found is None:
            raise FileNotFoundError('Could not find file "{}" in the '
                                    'current working directory nor '
                                    '6 levels up'.format(source))
        else:
            source = source_found

    with open(source) as f:
        try:
            raw = yaml.load(f, Loader=yaml.SafeLoader)
        except Exception as e:
            raise type(e)('yaml.load failed to parse your YAML file '
                          'fix syntax errors and try again') from e

    path = Path(source).resolve()

    return raw, path, _get_name(path)


def raw_preprocess(raw, path_to_raw):
    """
    Preprocess a raw dictionary. If a '_module' key exists, it
    will be expanded: first, try to locate a module with that name and resolve
    to their location (root __init__.py parent), if no module is found,
    interpret as a path to the project's root folder, checks that the folder
    actually exists. '{{here}}' is also allowed, which resolves to the
    path_to_raw, raises Exception if path_to_raw is None

    Returns
    -------
    preprocessed : dict
        Dict with preprocessed keys (empty dictionary it no special
        keys exist in raw)

    Parameters
    ----------
    raw : dict
        Raw data dictionary
    path_to_raw : str
        Path to file where dict was read from, it read from a dict, pass None
    """
    module = raw.get('_module')
    preprocessed = {}

    if module:

        if raw['_module'] == '{{here}}':

            if path_to_raw is not None:
                preprocessed['_module'] = path_to_raw.parent
            else:
                raise ValueError('_module cannot be {{here}} if '
                                 'not loaded from a file')
        else:
            try:
                module_spec = importlib.util.find_spec(module)
            except ValueError:
                # raises ValueError if passed "."
                module_spec = None

            if not module_spec:
                path_to_module = Path(module)

                if not (path_to_module.exists()
                        and path_to_module.is_dir()):
                    raise ValueError('Could not resolve _module "{}", '
                                     'failed to import as a module '
                                     'and is not a directory'
                                     .format(module))

            else:
                path_to_module = Path(module_spec.origin).parent

            preprocessed['_module'] = path_to_module

    return preprocessed


def find_env_w_name(name):
    """
    Find environment named 'env.{name}.yaml' by looking into the current
    directory and upper folders. If this fails, attempt to do the same
    for a file named just 'env.yaml'

    Returns
    -------
    path_to_env : pathlib.Path
        Path to environment file, None if no file could be found
    """
    path = find_env(name='env.{}.yaml'.format(name))

    if path is None:
        return find_env(name='env.yaml')
    else:
        return path


def find_env(name, max_levels_up=6):
    """
    Find environment by looking into the current folder and parent folders,
    returns None if no file was found otherwise pathlib.Path to the file
    """
    def levels_up(n):
        return chain.from_iterable(iglob('../' * i + '**')
                                   for i in range(n + 1))

    path_to_env = None

    for filename in levels_up(max_levels_up):
        p = Path(filename)

        if p.name == name:
            path_to_env = filename
            break

    return path_to_env


def _get_name(path_to_env):
    """
    Parse env.{name}.yaml to return name
    """
    filename = str(Path(path_to_env).name)

    err = ValueError('Wrong filename, must be either env.{name}.yaml '
                     'or env.yaml')

    elements = filename.split('.')

    if len(elements) == 2:
        # no name case
        env, _ = elements
        name = 'root'
    elif len(elements) > 2:
        # name
        env = elements[0]
        name = '.'.join(elements[1:-1])
    else:
        raise err

    if env != 'env':
        raise err

    return name

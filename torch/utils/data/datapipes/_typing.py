# Taking reference from official Python typing
# https://github.com/python/cpython/blob/master/Lib/typing.py

import collections
import functools
import inspect
import numbers
import sys
from typing import (Any, Dict, Iterator, Generic, List, Set, Tuple, TypeVar, Union,
                    get_type_hints)
from typing import _eval_type, _tp_cache, _type_check, _type_repr  # type: ignore[attr-defined]
from typing import ForwardRef

# TODO: Use TypeAlias when Python 3.6 is deprecated
# Please check [Note: TypeMeta and TypeAlias]
# In case of metaclass conflict due to ABCMeta or _ProtocolMeta
# For Python 3.9, only Protocol in typing uses metaclass
from abc import ABCMeta
from typing import _GenericAlias  # type: ignore[attr-defined, no-redef]

class GenericMeta(ABCMeta):  # type: ignore[no-redef]
    pass

import torch

class Integer(numbers.Integral):
    pass


class Boolean(numbers.Integral):
    pass


# Python 'type' object is not subscriptable
# Tuple[int, List, dict] -> valid
# tuple[int, list, dict] -> invalid
# Map Python 'type' to abstract base class
TYPE2ABC = {
    bool: Boolean,
    int: Integer,
    float: numbers.Real,
    complex: numbers.Complex,
    dict: Dict,
    list: List,
    set: Set,
    tuple: Tuple,
    None: type(None),
}


def issubtype(left, right, recursive=True):
    r"""
    Check if the left-side type is a subtype of the right-side type.
    If any of type is a composite type like `Union` and `TypeVar` with
    bounds, it would be expanded into a list of types and check all
    of left-side types are subtypes of either one from right-side types.
    """
    left = TYPE2ABC.get(left, left)
    right = TYPE2ABC.get(right, right)

    if right is Any or left == right:
        return True

    if isinstance(right, _GenericAlias):
        if getattr(right, '__origin__', None) is Generic:
            return True

    if right == type(None):
        return False

    # Right-side type
    constraints = _decompose_type(right)

    if len(constraints) == 0 or Any in constraints:
        return True

    if left is Any:
        return False

    # Left-side type
    variants = _decompose_type(left)

    # all() will return True for empty variants
    if len(variants) == 0:
        return False

    return all(_issubtype_with_constraints(variant, constraints, recursive) for variant in variants)


def _decompose_type(t, to_list=True):
    if isinstance(t, TypeVar):
        if t.__bound__ is not None:
            ts = [t.__bound__]
        else:
            # For T_co, __constraints__ is ()
            ts = list(t.__constraints__)
    elif hasattr(t, '__origin__') and t.__origin__ == Union:
        ts = t.__args__
    else:
        if not to_list:
            return None
        ts = [t]
    # Ignored: Generator has incompatible item type "object"; expected "Type[Any]"
    ts = list(TYPE2ABC.get(_t, _t) for _t in ts)  # type: ignore[misc]
    return ts


def _issubtype_with_constraints(variant, constraints, recursive=True):
    r"""
    Check if the variant is a subtype of either one from constraints.
    For composite types like `Union` and `TypeVar` with bounds, they
    would be expanded for testing.
    """
    if variant in constraints:
        return True

    # [Note: Subtype for Union and TypeVar]
    # Python typing is able to flatten Union[Union[...]] or Union[TypeVar].
    # But it couldn't flatten the following scenarios:
    #   - Union[int, TypeVar[Union[...]]]
    #   - TypeVar[TypeVar[...]]
    # So, variant and each constraint may be a TypeVar or a Union.
    # In these cases, all of inner types from the variant are required to be
    # extraced and verified as a subtype of any constraint. And, all of
    # inner types from any constraint being a TypeVar or a Union are
    # also required to be extracted and verified if the variant belongs to
    # any of them.

    # Variant
    vs = _decompose_type(variant, to_list=False)

    # Variant is TypeVar or Union
    if vs is not None:
        return all(_issubtype_with_constraints(v, constraints, recursive) for v in vs)

    # Variant is not TypeVar or Union
    if hasattr(variant, '__origin__') and variant.__origin__ is not None:
        v_origin = variant.__origin__
        # In Python-3.9 typing library untyped generics do not have args
        v_args = getattr(variant, "__args__", None)
    else:
        v_origin = variant
        v_args = None

    # Constraints
    for constraint in constraints:
        cs = _decompose_type(constraint, to_list=False)

        # Constraint is TypeVar or Union
        if cs is not None:
            if _issubtype_with_constraints(variant, cs, recursive):
                return True
        # Constraint is not TypeVar or Union
        else:
            # __origin__ can be None for plain list, tuple, ... in Python 3.6
            if hasattr(constraint, '__origin__') and constraint.__origin__ is not None:
                c_origin = constraint.__origin__
                if v_origin == c_origin:
                    if not recursive:
                        return True
                    # In Python-3.9 typing library untyped generics do not have args
                    c_args = getattr(constraint, "__args__", None)
                    if c_args is None or len(c_args) == 0:
                        return True
                    if v_args is not None and len(v_args) == len(c_args) and \
                            all(issubtype(v_arg, c_arg) for v_arg, c_arg in zip(v_args, c_args)):
                        return True
            # Tuple[int] -> Tuple
            else:
                if v_origin == constraint:
                    return True

    return False


def issubinstance(data, data_type):
    if not issubtype(type(data), data_type, recursive=False):
        return False

    # In Python-3.9 typing library __args__ attribute is not defined for untyped generics
    dt_args = getattr(data_type, "__args__", None)
    if isinstance(data, tuple):
        if dt_args is None or len(dt_args) == 0:
            return True
        if len(dt_args) != len(data):
            return False
        return all(issubinstance(d, t) for d, t in zip(data, dt_args))
    elif isinstance(data, (list, set)):
        if dt_args is None or len(dt_args) == 0:
            return True
        t = dt_args[0]
        return all(issubinstance(d, t) for d in data)
    elif isinstance(data, dict):
        if dt_args is None or len(dt_args) == 0:
            return True
        kt, vt = dt_args
        return all(issubinstance(k, kt) and issubinstance(v, vt) for k, v in data.items())

    return True


# [Note: TypeMeta and TypeAlias]
# In order to keep compatibility for Python 3.6, use Meta for the typing.
# TODO: When PyTorch drops the support for Python 3.6, it can be converted
# into the Alias system and using `__class_getiterm__` for DataPipe. The
# typing system will gain benefit of performance and resolving metaclass
# conflicts as elaborated in https://www.python.org/dev/peps/pep-0560/


class _DataPipeType:
    r"""
    Save type annotation in `param`
    """

    def __init__(self, param):
        self.param = param

    def __repr__(self):
        return _type_repr(self.param)

    def __eq__(self, other):
        if isinstance(other, _DataPipeType):
            return self.param == other.param
        return NotImplemented

    def __hash__(self):
        return hash(self.param)

    def issubtype(self, other):
        if isinstance(other.param, _GenericAlias):
            if getattr(other.param, '__origin__', None) is Generic:
                return True
        if isinstance(other, _DataPipeType):
            return issubtype(self.param, other.param)
        if isinstance(other, type):
            return issubtype(self.param, other)
        raise TypeError("Expected '_DataPipeType' or 'type', but found {}".format(type(other)))

    def issubtype_of_instance(self, other):
        return issubinstance(other, self.param)


# Default type for DataPipe without annotation
T_co = TypeVar('T_co', covariant=True)
_DEFAULT_TYPE = _DataPipeType(Generic[T_co])


class _DataPipeMeta(GenericMeta):
    r"""
    Metaclass for `DataPipe`. Add `type` attribute and `__init_subclass__` based
    on the type, and validate the return hint of `__iter__`.
    """
    type: _DataPipeType

    def __new__(cls, name, bases, namespace, **kwargs):
        if '__iter__' in namespace:
            hook_iterator(namespace, 'enumerate(DataPipe)#{}'.format(name))

        return super().__new__(cls, name, bases, namespace, **kwargs)  # type: ignore[call-overload]

        cls.__origin__ = None
        if 'type' in namespace:
            return super().__new__(cls, name, bases, namespace, **kwargs)  # type: ignore[call-overload]

        namespace['__type_class__'] = False
        #  For plain derived class without annotation
        for base in bases:
            if isinstance(base, _DataPipeMeta):
                return super().__new__(cls, name, bases, namespace, **kwargs)  # type: ignore[call-overload]

        namespace.update({'type': _DEFAULT_TYPE,
                          '__init_subclass__': _dp_init_subclass})
        return super().__new__(cls, name, bases, namespace, **kwargs)  # type: ignore[call-overload]

    def __init__(self, name, bases, namespace, **kwargs):
        super().__init__(name, bases, namespace, **kwargs)  # type: ignore[call-overload]

    # TODO: Fix isinstance bug
    @_tp_cache
    def _getitem_(self, params):
        if params is None:
            raise TypeError('{}[t]: t can not be None'.format(self.__name__))
        if isinstance(params, str):
            params = ForwardRef(params)
        if not isinstance(params, tuple):
            params = (params, )

        msg = "{}[t]: t must be a type".format(self.__name__)
        params = tuple(_type_check(p, msg) for p in params)

        if isinstance(self.type.param, _GenericAlias):
            orig = getattr(self.type.param, '__origin__', None)
            if isinstance(orig, type) and orig is not Generic:
                p = self.type.param[params]  # type: ignore[index]
                t = _DataPipeType(p)
                l = len(str(self.type)) + 2
                name = self.__name__[:-l]
                name = name + '[' + str(t) + ']'
                bases = (self,) + self.__bases__
                return self.__class__(name, bases,
                                      {'__init_subclass__': _dp_init_subclass,
                                       'type': t,
                                       '__type_class__': True})

        if len(params) > 1:
            raise TypeError('Too many parameters for {} actual {}, expected 1'.format(self, len(params)))

        t = _DataPipeType(params[0])

        if not t.issubtype(self.type):
            raise TypeError('Can not subclass a DataPipe[{}] from DataPipe[{}]'
                            .format(t, self.type))

        # Types are equal, fast path for inheritance
        if self.type == t:
            return self

        name = self.__name__ + '[' + str(t) + ']'
        bases = (self,) + self.__bases__

        return self.__class__(name, bases,
                              {'__init_subclass__': _dp_init_subclass,
                               '__type_class__': True,
                               'type': t})

    # TODO: Fix isinstance bug
    def _eq_(self, other):
        if not isinstance(other, _DataPipeMeta):
            return NotImplemented
        if self.__origin__ is None or other.__origin__ is None:  # type: ignore[has-type]
            return self is other
        return (self.__origin__ == other.__origin__  # type: ignore[has-type]
                and self.type == other.type)

    # TODO: Fix isinstance bug
    def _hash_(self):
        return hash((self.__name__, self.type))


def _simplify_obj_name(obj) -> str:
    """
    Simplify the display strings of objects for the purpose of rendering within DataPipe error messages.
    """
    default_str = str(obj)
    # Instead of showing <torch. ... .MapperIterDataPipe object at 0x.....>, return the class name
    if isinstance(type(obj), _DataPipeMeta):  # Only applies to DataPipes
        try:
            return str(obj.__class__.__qualname__)
        except Exception as _:
            return default_str
    elif inspect.isfunction(obj):
        return obj.__name__
    else:
        return default_str


def _generate_input_args_string(obj):
    """
    Generate a string for the input arguments of an object.
    """
    signature = inspect.signature(obj.__class__)
    input_param_names = set()
    for param_name, _ in signature.parameters.items():
        input_param_names.add(param_name)
    result = []
    for name, obj in inspect.getmembers(obj):
        if name in input_param_names:
            result.append((name, _simplify_obj_name(obj)))
    return ', '.join([f'{name}={value}' for name, value in result])


def hook_iterator(namespace, profile_name):

    def context():
        return torch.autograd.profiler.record_function(profile_name)

    class IteratorDecorator:
        '''Wrap the iterator return result by adding __next__'''
        def __init__(self, iterator):
            self.iterator = iterator

        def __iter__(self):
            return self

        def __next__(self):
            # TODO: Add try-except to in-place reduce traceback from the Exception
            # See: https://github.com/pytorch/data/issues/284
            with context():
                return next(self.iterator)

        def __getattr__(self, name):
            return getattr(self.iterator, name)

    func = namespace['__iter__']

    # ``__iter__`` of IterDataPipe is a generator function
    if inspect.isgeneratorfunction(func):
        @functools.wraps(func)
        def wrap_generator(*args, **kwargs):
            gen = func(*args, **kwargs)
            try:
                with context():
                    response = gen.send(None)
                while True:
                    request = yield response
                    with context():
                        response = gen.send(request)
            except StopIteration as e:
                return e.value
            except Exception as e:
                # TODO: Simplify the traceback message to skip over `response = gen.send(None)`
                #       Part of https://github.com/pytorch/data/issues/284
                datapipe = args[0]
                msg = "thrown by __iter__ of"
                full_msg = f"{msg} {datapipe.__class__.__name__}({_generate_input_args_string(datapipe)})"
                if len(e.args) >= 1 and msg not in e.args[0]:
                    e.args = (e.args[0] + f'\nThis exception is {full_msg}',) + e.args[1:]
                raise

        namespace['__iter__'] = wrap_generator
    else:
        # IterDataPipe is an iterator with both ``__iter__`` and ``__next__``
        if '__next__' in namespace:
            next_func = namespace['__next__']

            @functools.wraps(next_func)
            def wrap_next(*args, **kwargs):
                with context():
                    return next_func(*args, **kwargs)

            namespace['__next__'] = wrap_next
        # ``__iter__`` of IterDataPipe returns an iterator other than self
        else:
            @functools.wraps(func)
            def wrap_iter(*args, **kwargs):
                iter_ret = func(*args, **kwargs)
                return IteratorDecorator(iter_ret)

            namespace['__iter__'] = wrap_iter


def _dp_init_subclass(sub_cls, *args, **kwargs):
    # Add function for datapipe instance to reinforce the type
    sub_cls.reinforce_type = reinforce_type

    # TODO:
    # - add global switch for type checking at compile-time

    # Ignore internal type class
    if getattr(sub_cls, '__type_class__', False):
        return

    # Check if the string type is valid
    if isinstance(sub_cls.type.param, ForwardRef):
        base_globals = sys.modules[sub_cls.__module__].__dict__
        try:
            param = _eval_type(sub_cls.type.param, base_globals, locals())
            sub_cls.type.param = param
        except TypeError as e:
            raise TypeError("{} is not supported by Python typing"
                            .format(sub_cls.type.param.__forward_arg__)) from e

    if '__iter__' in sub_cls.__dict__:
        iter_fn = sub_cls.__dict__['__iter__']
        hints = get_type_hints(iter_fn)
        if 'return' in hints:
            return_hint = hints['return']
            # Plain Return Hint for Python 3.6
            if return_hint == Iterator:
                return
            if not (hasattr(return_hint, '__origin__') and
                    (return_hint.__origin__ == Iterator or
                     return_hint.__origin__ == collections.abc.Iterator)):
                raise TypeError("Expected 'Iterator' as the return annotation for `__iter__` of {}"
                                ", but found {}".format(sub_cls.__name__, _type_repr(hints['return'])))
            data_type = return_hint.__args__[0]
            if not issubtype(data_type, sub_cls.type.param):
                raise TypeError("Expected return type of '__iter__' as a subtype of {}, but found {}"
                                " for {}".format(sub_cls.type, _type_repr(data_type), sub_cls.__name__))


def reinforce_type(self, expected_type):
    r"""
    Reinforce the type for DataPipe instance. And the 'expected_type' is required
    to be a subtype of the original type hint to restrict the type requirement
    of DataPipe instance.
    """
    if isinstance(expected_type, tuple):
        expected_type = Tuple[expected_type]
    _type_check(expected_type, msg="'expected_type' must be a type")

    if not issubtype(expected_type, self.type.param):
        raise TypeError("Expected 'expected_type' as subtype of {}, but found {}"
                        .format(self.type, _type_repr(expected_type)))

    self.type = _DataPipeType(expected_type)
    return self

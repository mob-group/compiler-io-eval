from typing import Union, NewType, Callable, Optional

ScalarValue = Union[int, float, bool, str]
ArrayValue = Union[str, list[ScalarValue]]
SomeValue = Union[ScalarValue, ArrayValue]
AnyValue = Union[SomeValue, None]

Name = NewType("Name", str)
Parser = NewType("Parser", Callable)

ParameterMapping = dict[Name, SomeValue]

# ERROR TYPES

class InvalidImplementationError(Exception):
    pass


class UnsatisfiedDependencyError(Exception):
    pass


class ParseError(Exception):
    def __init__(self, message: str, reference_name: Optional[str] = None):
        self.reference = reference_name
        self.message = message

    def __str__(self):
        return f"{self.reference}: {self.message}"

class FunctionRunError(Exception):
    pass


class CompilationError(Exception):
    def __init__(self, compilable: str, library: str):
        self.compilable = compilable
        self.library = library

    def __str__(self):
        return f"issue compiling {self.compilable} into {self.library}"


class UnsupportedTypeError(Exception):
    def __init__(self, unsupported):
        self.unsupported = unsupported

    def __str__(self):
        return f"attempted to use unsupported type: {self.unsupported}"

import os.path
import re
from dataclasses import dataclass, asdict
from enum import Enum
from json import dumps
from sys import stderr
from typing import *

import lumberjack
from helper_types import *
from typing import Dict, List, Tuple, Set

def removeprefix(text, prefix):
    if text.startswith(prefix):
        return text[len(prefix):]
    return text  # or whatever


class ParseIssue(Enum):
    """
    Issues in a parsed reference implementation.

    Can be matched if smarter error handling is desired, or a simple error message
    can be accessed using :code:`issue.value`
    """
    ArrayReturnType = "Return type must be `void' or scalar"
    MultiLevelPointer = "Multi-level pointers are not supported"
    ScalarOutputParameter = "Output parameters must be pointers"
    ScalarGivenSize = "Only array parameters can be given a size"
    GivenInvalidSize = "Sizes must be a valid type"
    UnsizedArrayParameter = "All unterminated arrays must be given a size"
    ReferenceSignatureMismatch = "The signatures in `ref.c' and `props' differ"
    InvalidIdentifierName = "All names must be valid C identifiers"
    InvalidConstraint = "A forbidden constraint was given"

    @staticmethod
    def ignorable():
        return {
            ParseIssue.ScalarGivenSize,
        }


@dataclass
class CType:
    """
    A wrapper for a C type.
    """
    contents: str
    pointer_level: int

    @staticmethod
    def parse(type_sig: str):
        """
        Build a type instance from a type signature.

        Type signatures can look like: :code:`int`, :code:`int *`, :code:`char*`, :code:`void ** *`, etc.

        No checking is done here to determine whether the type is valid.

        :param type_sig: the type signature
        :return: an instance of that type
        """
        type_sig = type_sig.strip()

        if '*' in type_sig:
            ptr_idx = type_sig.index('*')
            contents = type_sig[:ptr_idx].rstrip()
            pointers = type_sig[ptr_idx:]

            pointer_level = sum(1 for c in pointers if c == "*")
        else:
            contents = type_sig.strip()
            pointer_level = 0

        if pointer_level > 1:
            raise UnsupportedTypeError("multi-level pointers")

        return CType(contents, pointer_level)

    def __str__(self):
        return f"{self.contents}{'*' * self.pointer_level}"


@dataclass
class CParameter:
    """
    A wrapper for a parameter.
    """
    name: Name
    type: CType

    @staticmethod
    def parse(param: str):
        """
        Builds a CParameter instance.

        Does not check if the type is a valid name, just separates it from the type.

        :param param: the parameter definition
        :return: an instance from that definition
        """
        m = re.match("((?:int|char|float|double|bool|void)[* ]+)(.*)", param)
        if m is None:
            raise ParseError("invalid parameter")

        c_type, name = m.groups()

        type_info = CType.parse(c_type)
        return CParameter(name, type_info)

    def __str__(self):
        return f"{self.type} {self.name}"


@dataclass
class FunctionSignature:
    """
    A C function's full signature
    """
    name: Name
    type: CType
    parameters: List[CParameter]

    @staticmethod
    def parse(sig: str):
        """
        Build a FunctionSignature instance from a signature string.

        This string looks like:

        .. code-block:: c

            [func type] [func name]([parameter], ...)

        :param sig: the signature
        :return: the instance built from that signature
        """
        m = re.match(r"(.*)\((.*)\)", sig)
        if m is None:
            raise ParseError("could not parse function signature")

        func_def = CParameter.parse(m[1].strip())
        params = [param.strip() for param in m[2].split(",")]

        return FunctionSignature(func_def.name,
                                 func_def.type,
                                 [CParameter.parse(param) for param in params])

    def __str__(self):
        return f"{self.name}({', '.join(str(param) for param in self.parameters)}) -> {self.type}"

    def c_sig(self) -> str:
        """
        The function signature as it would appear in C.

        Note all pointer types look like :code:`type* name` (as opposed to :code:`type *name`)

        :return: the signature string
        """
        return f"{self.type} {self.name}({', '.join(str(param) for param in self.parameters)})"


@dataclass
class ParamSize:
    """
    The base type for sizes of array parameters
    """
    arr: Name

    @staticmethod
    def parse(size: str):
        """
        Parses a size description into the correct subclass for the size

        TODO: change this to something better, fails on (the comma in) inputs like "size x, { sum(x*y for x, y in zip(..)) }
        :param size: the size description
        :return: the actual size object built from that description
        """
        size = size.lstrip()

        parts = re.finditer(r"\s*,\s*", size)

        part = next(parts)
        arr_end, next_start = part.span()

        arr = size[:part.span()[0]]
        try:
            part = next(parts)
            val_end, expr_start = part.span()

            val = int(size[next_start:val_end])
            expr = size[expr_start:].rstrip()

            assert expr.startswith("{") and expr.endswith("}")

            return ExprSize(Name(arr), val, expr[1:-1])
        except StopIteration:
            try:
                val = int(size[next_start:])
                return ConstSize(Name(arr), val)
            except ValueError:
                var = size[next_start:].rstrip()

                if var.startswith("{"):
                    assert var.endswith("}")

                    return SimpleExprSize(Name(arr), var[1:-1])
                else:
                    return VarSize(Name(arr), Name(var))

    def evaluate(self, values: dict, initial: bool = False) -> Optional[int]:
        """
        Determine the actual size of the array

        This method can be used to calculate the size of the initial native Python value being fed into a function,
        as well as the size a foreign array needs to be to contain a value it will later hold.
        Note this does *NOT* include the extra space needed for a '\0' in a string.

        :param values: the values already determined for the parameters
        :param initial: set to :code:`True` for the size of the native value,
        otherwise the size is for the foreign array.
        :return: the size of the array
        """
        raise NotImplementedError("This is an abstract method, use the corresponding method in a subtype")


@dataclass
class VarSize(ParamSize):
    """
    Denotes an association between a array parameter, and a scalar parameter containing the array's size
    """
    arr: Name
    var: Name

    def evaluate(self, values: dict, initial: bool = False) -> Optional[int]:
        var = self.var
        assert var in values

        return values.get(var)


@dataclass
class ConstSize(ParamSize):
    """
    Denotes a constant size on an array parameter

    Note this constant can also be treated as a *maximum* size.
    """
    arr: Name
    size: int

    def evaluate(self, values: dict, initial: bool = False) -> Optional[int]:
        return self.size


@dataclass
class ExprSize(ParamSize):
    """
    Denotes a complex sizing scheme, typically used for output strings

    Contains the size for the initial size of the array (for generation),
    and an expression to calculate the maximum size the array can be (to create a foreign array large enough).
    """
    arr: Name
    init: int
    expr: str

    def evaluate(self, values: dict, initial: bool = False) -> Optional[int]:
        if initial:
            return self.init
        else:
            size = eval(self.expr, dict(values))
            assert size is not None and isinstance(size, int)

            return size


@dataclass
class SimpleExprSize(ParamSize):
    """
    Denotes an array size calculated from an expression

    This is useful to encode a more complicated relationship between parameters,
    for example in matrices where the size of an m x n array M needs to be { m * n }
    """
    arr: Name
    expr: str

    def evaluate(self, values: dict, initial: bool = False) -> Optional[int]:
        size = eval(self.expr, dict(values))
        assert size is not None and isinstance(size, int)

        return size


class Constraint:
    """
    Base class for a constraint property
    """

    @staticmethod
    def parse(constraint: str):
        """
        Extracts a constraint from a description of a constraint,
        determining the type of constraint and embedding it in an object of the corresponding subclass

        :param constraint: the constraint description
        :return: the constraint object
        """
        constraint = constraint.lstrip()

        if constraint.startswith("{"):
            constraint = constraint.rstrip()

            assert constraint.endswith("}")

            return GlobalContstraint(constraint[1:-1])
        else:
            word_boundary = constraint.index(" ")
            var = constraint[:word_boundary]

            constraint = constraint[word_boundary:].lstrip()

            word_boundary = constraint.index(" ")
            op, val = constraint[:word_boundary], constraint[word_boundary:].strip()

            assert op in {">", "<", ">=", "<=", "==", "!="}

            return ParamConstraint(Name(var), op, val)

    def satisfied(self, inputs: ParameterMapping) -> bool:
        """
        Determines if a constraint is met by a particular instance of inputs

        :param inputs: the parameters to check
        :return: whether or not these inputs satisfy the constraint
        """
        raise NotImplementedError("This is an abstract method, use the corresponding method in a subtype")


@dataclass
class ParamConstraint(Constraint):
    """
    A constraint on a single parameter
    """
    var: Name
    op: str
    val: str

    def satisfied(self, inputs: ParameterMapping) -> bool:
        return eval(f"{self.var} {self.op} {self.val}", dict(inputs))

    @property
    def value(self) -> SomeValue:
        """
        Evaluates the value of the constraint

        :return: the value in the constraint
        """
        return eval(self.val)


@dataclass
class GlobalContstraint(Constraint):
    """
    A general constraint on any number of parameters
    """
    predicate: str

    def satisfied(self, inputs: ParameterMapping) -> bool:
        return eval(self.predicate, dict(inputs))


@dataclass
class FunctionInfo:
    """
    A wrapper for additional information found in a function's *props* file.

    This includes the names of any output parameters, and the given sizes of any array parameters.
    """
    outputs: List[Name]
    sizes: List[ParamSize]
    constraints: List[Constraint]

    @staticmethod
    def parse(info: List[str]):
        """
        Build a FunctionInfo instance from a list of description strings.
        These strings may be describing either sizes or outputs.


        :param info: the description strings
        :return: the instance containing the information
        """
        outputs = []
        sizes = []
        constraints = []

        for line in info:
            if line.startswith("output"):
                outputs.append(Name(removeprefix(line, "output").strip()))
            elif line.startswith("size"):
                size = ParamSize.parse(removeprefix(line, "size").strip())
                sizes.append(size)
            elif line.startswith("constraint"):
                constraint = Constraint.parse(removeprefix(line, "constraint").strip())
                constraints.append(constraint)
            else:
                raise ParseError(f"invalid directive in props: {line.strip()}")

        return FunctionInfo(outputs, sizes, constraints)

    def is_output(self, param: CParameter) -> bool:
        return param.name in self.outputs

    def size(self, param: CParameter) -> Optional[ParamSize]:
        return {size.arr: size for size in self.sizes}.get(param.name)


@dataclass
class FunctionProps:
    """
    Contains all information from a function's *props* file.

    This includes the signature and any additional information about the parameters.
    """
    sig: FunctionSignature
    arr_info: FunctionInfo

    @staticmethod
    def parse(props_file: str):
        """
        Build a FunctionProps instance from a *props* file.

        :param props_file: the path to the *props* file
        :return: the instance built from that file
        """
        with open(props_file, "r") as props:
            sig = FunctionSignature.parse(props.readline())
            rest = FunctionInfo.parse(props.readlines())

        return FunctionProps(sig, rest)


@dataclass
class CReference:
    """
    Contains all relevant information from a function's *ref.c* file.

    This is the :code:`#includes` found in the file, as well as the C implementation of the function itself.
    """
    includes: List[str]
    code: str

    @staticmethod
    def parse(ref_file: str):
        """
        Build a CReference instance from a given *ref.c* file.

        :param ref_file: the path to the *ref.c* file
        :return: the instance built from that file
        """
        with open(ref_file, "r") as ref:
            includes = []

            # go through each line and:
            #   1. store includes
            #   2. ignore anything other than the function
            #   3. store the function code
            line = ""  # this is just to ensure line has SOME value, to shut the warning up
            for line in ref:
                line = line.lstrip()
                if re.match("(int|float|double|char|bool|void)", line):
                    break  # assumes everything from here is the actual function

                if line.startswith("#include"):
                    includes.append(line.rstrip())

            func = line + ref.read()

        return CReference(includes, func)


@dataclass
class FunctionReference:
    """
    Wrapper for all information about a given function.
    """
    signature: FunctionSignature
    info: FunctionInfo
    reference: CReference

    @property
    def type(self):
        return self.signature.type

    @property
    def parameters(self):
        return self.signature.parameters

    def outputs(self):
        return [parameter for parameter in self.parameters if self.info.is_output(parameter)]

    @property
    def name(self):
        return self.signature.name

    @property
    def code(self):
        return self.reference.code

    @staticmethod
    def parse(prog_name: str):
        """
        Build a FunctionReference from an actual C function.
        This function must have a directory containing *ref.c* and *props* files.

        :param prog_name: the path to the function directory
        :return: the instance built for that function
        """
        path = os.path.expanduser(prog_name)

        try:
            props = FunctionProps.parse(os.path.join(path, "props"))
            ref = CReference.parse(os.path.join(path, "ref.c"))
        except ParseError as e:
            raise ParseError(e.message, reference_name=os.path.split(prog_name)[1])

        return FunctionReference(props.sig, props.arr_info, ref)

    def issues(self, fix: bool = False) -> Set[ParseIssue]:
        """
        Check this FunctionReference for any issues.

        :param fix: set to :code:`True` to try and fix any issues that are encountered
        :return: all issues found in the function
        """
        issues = set()

        if self.type.pointer_level != 0:
            issues.add(ParseIssue.ArrayReturnType)

        # building lookup tables
        param_dict = dict()
        array_params = set()
        scalar_params = set()
        for param in self.parameters:
            name = param.name
            c_type = param.type

            param_dict[name] = c_type
            if c_type.pointer_level == 0:
                scalar_params.add(name)
            elif c_type.pointer_level == 1:
                array_params.add(name)
            else:
                issues.add(ParseIssue.MultiLevelPointer)

            # this is a SUPER simplified version of checking for valid C identifiers
            # doesn't take keywords etc. into consideration
            m = re.match(r"^[a-zA-Z_]\w*$", name, flags=re.ASCII)
            if not m or m[0] != name:
                issues.add(ParseIssue.InvalidIdentifierName)

        for constraint in self.info.constraints:
            if not isinstance(constraint, ParamConstraint):
                continue

            if (constraint.var in array_params or param_dict[
                constraint.var].contents == "char") and constraint.op not in {"==", "!="}:
                issues.add(ParseIssue.InvalidConstraint)

        for output in self.info.outputs:
            if param_dict[output].pointer_level == 0:
                if fix:
                    self.info.outputs.remove(output)
                else:
                    issues.add(ParseIssue.ScalarOutputParameter)

        sized = set()
        for size in self.info.sizes:
            array = size.arr
            sized.add(array)

            if array not in array_params:
                if fix:
                    self.info.sizes.remove(size)
                else:
                    issues.add(ParseIssue.ScalarGivenSize)

            if isinstance(size, VarSize):
                var = size.var

                if param_dict[var].contents not in {"int"}:
                    issues.add(ParseIssue.GivenInvalidSize)
            elif isinstance(size, ConstSize):
                if size.size < 0:
                    issues.add(ParseIssue.GivenInvalidSize)
            elif isinstance(size, ExprSize):
                if size.init < 0:
                    issues.add(ParseIssue.GivenInvalidSize)

        for array in array_params - sized:
            if fix and param_dict[array] == CType("char", 1):
                default_str_size = 100
                self.info.sizes.append(ConstSize(array, default_str_size))
            else:
                issues.add(ParseIssue.UnsizedArrayParameter)

        code = self.code
        ref_signature = FunctionSignature.parse(code[:code.find("{")])

        if ref_signature != self.signature:
            # try and fix here if possible
            issues.add(ParseIssue.ReferenceSignatureMismatch)

        return issues

    def validate(self, issues, ignorable: Set[issues] = None):
        if ignorable is None:
            ignorable = ParseIssue.ignorable()

        if issues - ignorable:
            raise ParseError("parse contained issues", reference_name=self.name)

    def show_issues(self, issues: Set[ParseIssue], verbose: bool = False, ignore_good: bool = False) -> None:
        """
        Write any issues in the function to stderr

        :param issues: issues to show
        :param verbose: set to :code:`True` to include a full breakdown of any issues found
        :param ignore_good: set to :code:`True` to write to stderr even if no issues are found
        """
        if issues:
            print(f"error: {self.name} is broken!", file=stderr)
            for issue in issues:
                print(f" - {issue.value}", file=stderr)

            if verbose:
                print(dumps(asdict(self), indent=4) + "\n", file=stderr)
        elif not ignore_good:
            print(f"{self.name} is good", file=stderr)

    def log_issues(self, issues: Set[ParseIssue]):
        """
        Write any issues found to the error log

        :param issues: any issues found
        """
        if not issues:
            return

        logger = lumberjack.getLogger("error")
        msg = f"{self.name} has issues: [{', '.join(issue.name for issue in issues)}]"

        logger.warning(msg)


def show_all(base_path: str) -> None:
    """
    Parse and show the C signature for all functions in a given directory.

    Also flags errors if they occur, writing the results to stderr.

    :param base_path: the path to the directory containing all of the functions
    """
    base_path = os.path.expanduser(base_path)

    for directory in os.listdir(base_path):
        # breaking these up cos one big if was ugly
        if directory.startswith("__"):
            continue

        if directory.startswith("."):
            continue

        # building out the proper path to the function
        dir_path = os.path.join(base_path, directory)

        if not os.path.isdir(dir_path):
            continue

        if "ref.c" not in os.listdir(dir_path):
            continue

        if "props" not in os.listdir(dir_path):
            continue

        parsed = FunctionReference.parse(dir_path)
        parsed.show_issues(parsed.issues(), ignore_good=True)
        print(parsed.signature.c_sig())


def show_single(path_to_ref: str):
    """
    Parse and display the signature for a single program.

    Signature is given in functional form, and the full information is given if issues are found.

    :param path_to_ref: the reference directory
    """
    contents = FunctionReference.parse(path_to_ref)
    issues = contents.issues(fix=True)

    if issues:
        contents.show_issues(issues, verbose=True)
    else:
        print(dumps(asdict(contents), indent=4))


def load_reference(path_to_reference: str, log_issues: Callable = FunctionReference.log_issues) -> FunctionReference:
    """
    Creates a reference from a reference directory

    This will check the reference is valid, and will fail if the reference is not.

    :param path_to_reference: the reference directory
    :param log_issues: the method used to log any issues
    :return: the reference built from that directory
    """
    func = FunctionReference.parse(path_to_reference)
    issues = func.issues(fix=True)

    log_issues(func, issues)
    func.validate(issues)

    return func


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser()

    parser.add_argument("-a", "--all", help="display and debug all available references", action="store_true")
    parser.add_argument("program", nargs="?", help="parse and output the given program")
    parser.add_argument("-p", "--path", help="specify root path to example", default=".")

    args = parser.parse_args()

    if (args.program is None) != args.all:  # this is confusing as hell, either program is set, or all is (XOR)
        parser.print_usage(file=stderr)
        print(f"{parser.prog}: error: exactly one argument must be set from (--all, program)", file=stderr)
        exit(1)

    if args.all:
        show_all(args.path)
    else:
        show_single(os.path.join(args.path, args.program))

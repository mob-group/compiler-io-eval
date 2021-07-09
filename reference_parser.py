import os.path
import re
from dataclasses import dataclass, asdict
from enum import Enum
from json import dumps
from sys import stderr
from typing import *

import lumberjack


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

    @staticmethod
    def ignorable():
        return {
            ParseIssue.ArrayReturnType,
            ParseIssue.ScalarGivenSize,
        }


class ParseError(Exception):
    pass


class UnsupportedTypeError(Exception):
    def __init__(self, unsupported):
        self.unsupported = unsupported

    def __str__(self):
        return f"attempted to use unsupported type: {self.unsupported}"


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
    name: str
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
    name: str
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
    Denotes an association between a array parameter, and a scalar parameter containing the array's size.
    Additionally, for instance in strings, it can contain the way to derive the size of the array from
    multiple parameters.
    """
    array: str
    size: str
    max: Optional[int] = None

    @staticmethod
    def parse(size: str):
        """
        Build a ParamSize instance from a size description string.

        These strings are in the form given in *props* files, e.g.

        "size arr_name, scalar_name"

        Also supports complex size format, e.g.

        "size arr_name, max, { expr }"

        Here *expr* is a Python expressions that can be evaluated,
        and *max* is the maximum length of the **initial** value.

        The value of the expression (val) is always used to specify the
        size of the C array, even if val > max.
        This is because max only caps the size of the initial value of
        the array **in Python**, it is never passed through to C.

        :param size: the description
        :return: the ParamSize instance
        """
        size = size.removeprefix("size").strip()

        split = size.index(",")

        arr_name = size[:split].strip()
        assert arr_name

        size = size[split + 1:].strip()

        if "," not in size:
            # simple format
            return ParamSize(arr_name, size)
        else:
            # in complex format

            # split again to find max and size
            split = size.index(",")

            arr_max = int(size[:split].strip())
            arr_size = size[split + 1:].strip()

            # check fields are valid
            if not (arr_size.startswith("{") and arr_size.endswith("}")):
                raise ParseError("invalid complex size expression given")

            # remove braces from expr and check it is still non-empty
            arr_size = arr_size[1:-1].strip()
            if not arr_size:
                raise ParseError("empty expression given in complex size")

            return ParamSize(arr_name, arr_size, max=arr_max)


@dataclass
class FunctionArrayInfo:
    """
    A wrapper for additional information found in a function's *props* file.

    This includes the names of any output parameters, and the given sizes of any array parameters.
    """
    outputs: List[str]
    sizes: List[ParamSize]

    @staticmethod
    def parse(outputs: list[str], sizes: list[str]):
        """
        Build a FunctionArrayInfo instance from a list of description strings.
        These strings may be describing either sizes or outputs.


        :param info: the description strings
        :return: the instance containing the information
        """
        return FunctionArrayInfo(outputs, [ParamSize.parse(size) for size in sizes])

    def is_output(self, param: CParameter) -> bool:
        return param.name in self.outputs

    def size(self, param: CParameter) -> str:
        return {size.array: size.size for size in self.sizes}.get(param.name)


@dataclass
class FunctionConstraints:
    constraints: list[str]

    def satisfied(self, **kwargs):
        return all(eval(constraint) for constraint in self.constraints)

    @staticmethod
    def parse(constraints: list[str]):
        def parse_single(constraint: str):
            return constraint.strip().removeprefix("{").removesuffix("}")

        return FunctionConstraints([parse_single(constraint) for constraint in constraints])


@dataclass
class FunctionProps:
    """
    Contains all information from a function's *props* file.

    This includes the signature and any additional information about the parameters.
    """
    sig: FunctionSignature
    arr_info: FunctionArrayInfo
    constraints: FunctionConstraints

    @staticmethod
    def parse(props_file: str):
        """
        Build a FunctionProps instance from a *props* file.

        :param props_file: the path to the *props* file
        :return: the instance built from that file
        """
        with open(props_file, "r") as props:
            sig = FunctionSignature.parse(props.readline())
            rest = props.readlines()

        outputs = []
        sizes = []
        constraints = []

        for line in rest:
            if line.startswith("output"):
                outputs.append(line.removeprefix("output").strip())
            elif line.startswith("size"):
                sizes.append(line.removeprefix("size").strip())
            elif line.startswith("constrain"):
                constraints.append(line.removeprefix("constrain").strip())
            else:
                raise ParseError("invalid directive in props")

        return FunctionProps(sig, FunctionArrayInfo.parse(outputs, sizes), FunctionConstraints.parse(constraints))


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
    info: FunctionArrayInfo
    constraints: FunctionConstraints
    reference: CReference

    @property
    def type(self):
        return self.signature.type

    def parameters(self, safe_order=False) -> List[CParameter]:
        if not safe_order:
            return self.signature.parameters

        sized = []
        params = []
        for parameter in self.signature.parameters:
            if self.info.size(parameter) is None:
                params.append(parameter)
            else:
                sized.append(parameter)

        for parameter in sized:
            params.append(parameter)

        return params

    def outputs(self):
        return [parameter for parameter in self.parameters() if self.info.is_output(parameter)]

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
        props = FunctionProps.parse(os.path.join(path, "props"))
        ref = CReference.parse(os.path.join(path, "ref.c"))

        return FunctionReference(props.sig, props.arr_info, props.constraints, ref)

    def issues(self) -> Set[ParseIssue]:
        """
        Check this FunctionReference for any issues.

        :return: all issues found in the function
        """
        issues = set()

        if self.type.pointer_level != 0:
            issues.add(ParseIssue.ArrayReturnType)

        # building lookup tables
        param_dict = dict()
        array_params = set()
        scalar_params = set()
        for param in self.parameters():
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

        for output in self.info.outputs:
            if param_dict[output].pointer_level == 0:
                issues.add(ParseIssue.ScalarOutputParameter)

        sized = set()
        for size in self.info.sizes:
            array = size.array
            var = size.size
            sized.add(array)

            if array not in array_params:
                issues.add(ParseIssue.ScalarGivenSize)

            if size.max is None and param_dict[var].contents not in {"int"}:
                issues.add(ParseIssue.GivenInvalidSize)

        for array in array_params - sized:
            if param_dict[array].contents not in {"char"}:
                issues.add(ParseIssue.UnsizedArrayParameter)

        code = self.code
        ref_signature = FunctionSignature.parse(code[:code.find("{")])

        if ref_signature != self.signature:
            issues.add(ParseIssue.ReferenceSignatureMismatch)

        return issues

    def validate(self, issues, ignorable: set[issues] = None):
        if ignorable is None:
            ignorable = ParseIssue.ignorable()

        if issues - ignorable:
            raise ParseError(f"parse of {self.name} contained issues")

    def show_issues(self, issues: set[ParseIssue], verbose: bool = False, ignore_good: bool = False) -> None:
        """
        Write any issues in the function to stderr

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

    def log_issues(self, issues: set[ParseIssue]) -> None:
        if not issues:
            return

        logger = lumberjack.getLogger("error")
        msg = f"{self.name} has issues: [{', '.join(issue.name for issue in issues)}]"

        logger.warn(msg)


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
        parsed.show_issues(ignore_good=True)
        print(parsed.signature.c_sig())


def show_single(path_to_ref: str) -> None:
    """
    Parse and display the signature for a single program.

    Signature is given in functional form, and the full information is given if issues are found.

    :param base_path: the full path to the directory containing a function
    :param prog_name: the name of the function directory
    """
    contents = FunctionReference.parse(path_to_ref)
    issues = contents.issues()

    print(dumps(asdict(contents), indent=4))
    contents.show_issues(issues, verbose=True)


def load_reference(path_to_reference: str, log_issues: Callable = FunctionReference.log_issues) -> FunctionReference:
    func = FunctionReference.parse(path_to_reference)
    issues = func.issues()

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

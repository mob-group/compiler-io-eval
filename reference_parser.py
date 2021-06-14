import re
from typing import *
import os.path
from sys import stderr
from json import dumps
from enum import Enum
from dataclasses import dataclass, asdict


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
            raise Exception("multi-level pointers are not supported")

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
            raise Exception("invalid parameter")

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
            raise Exception("broken...")

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
    Denotes an association between a array parameter, and a scalar parameter containing the array's size
    """
    array: str
    var: str

    @staticmethod
    def parse(size: str):
        """
        Build a ParamSize instance from a size description string.

        These strings are in the form given in *props* files, e.g.

        "size arr_name scalar_name"

        :param size: the description
        :return: the ParamSize instance
        """
        parts = size.removeprefix("size").strip().split(",")

        assert (len(parts) == 2)

        return ParamSize(parts[0].strip(), parts[1].strip())


@dataclass
class FunctionArrayInfo:
    """
    A wrapper for additional information found in a function's *props* file.

    This includes the names of any output parameters, and the given sizes of any array parameters.
    """
    outputs: List[str]
    sizes: List[ParamSize]

    @staticmethod
    def parse(info: List[str]):
        """
        Build a FunctionArrayInfo instance from a list of description strings.
        These strings may be describing either sizes or outputs.


        :param info: the description strings
        :return: the instance containing the information
        """
        outputs = []
        sizes = []

        for line in info:
            if line.startswith("output"):
                outputs.append(line.removeprefix("output").strip())
            elif line.startswith("size"):
                size = ParamSize.parse(line.removeprefix("size").strip())
                sizes.append(size)
            else:
                raise Exception("very bad")

        return FunctionArrayInfo(outputs, sizes)


@dataclass
class FunctionProps:
    """
    Contains all information from a function's *props* file.

    This includes the signature and any additional information about the parameters.
    """
    sig: FunctionSignature
    arr_info: FunctionArrayInfo

    @staticmethod
    def parse(props_file: str):
        """
        Build a FunctionProps instance from a *props* file.

        :param props_file: the path to the *props* file
        :return: the instance built from that file
        """
        with open(props_file, "r") as props:
            sig = FunctionSignature.parse(props.readline())
            rest = FunctionArrayInfo.parse(props.readlines())

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
    info: FunctionArrayInfo
    reference: CReference

    @property
    def type(self):
        return self.signature.type

    @property
    def parameters(self):
        return self.signature.parameters

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

        return FunctionReference(props.sig, props.arr_info, ref)

    def validate(self) -> Set[ParseIssue]:
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

        for output in self.info.outputs:
            if param_dict[output].pointer_level == 0:
                issues.add(ParseIssue.ScalarOutputParameter)

        sized = set()
        for size in self.info.sizes:
            array = size.array
            var = size.var
            sized.add(array)

            if array not in array_params:
                issues.add(ParseIssue.ScalarGivenSize)

            if param_dict[var].contents not in {"int"}:
                issues.add(ParseIssue.GivenInvalidSize)

        for array in array_params - sized:
            if param_dict[array].contents not in {"char"}:
                issues.add(ParseIssue.UnsizedArrayParameter)

        code = self.code
        ref_signature = FunctionSignature.parse(code[:code.find("{")])

        if ref_signature != self.signature:
            issues.add(ParseIssue.ReferenceSignatureMismatch)

        return issues

    def show_issues(self, verbose: bool = False, ignore_good: bool = False) -> None:
        """
        Write any issues in the function to stderr

        :param verbose: set to :code:`True` to include a full breakdown of any issues found
        :param ignore_good: set to :code:`True` to write to stderr even if no issues are found
        """
        issues = self.validate()

        if issues:
            stderr.write(f"error: {self.name} is broken!\n")
            for issue in issues:
                stderr.write(f" - {issue.value}\n")

            if verbose:
                stderr.write(dumps(asdict(self), indent=4) + "\n\n")
        elif not ignore_good:
            stderr.write(f"{self.name} is good\n")


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


def show_single(base_path: str, prog_name: str) -> None:
    """
    Parse and display the signature for a single program.

    Signature is given in functional form, and the full information is given if issues are found.

    :param base_path: the full path to the directory containing a function
    :param prog_name: the name of the function directory
    """
    contents = FunctionReference.parse(os.path.join(base_path, prog_name))
    print(dumps(asdict(contents), indent=4))
    contents.show_issues(verbose=True)


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser()

    parser.add_argument("-a", "--all", help="display and debug all available references", action="store_true")
    parser.add_argument("program", nargs="?", help="parse and output the given program")
    parser.add_argument("-p", "--path", help="path to example directory", default=".")

    args = parser.parse_args()

    if (args.program is None) != args.all:  # this is confusing as hell, either program is set, or all is (XOR)
        parser.print_usage(file=stderr)
        stderr.write(f"{parser.prog}: error: exactly one argument must be set from (--all, program)\n")
        exit(1)

    if args.all:
        show_all(args.path)
    else:
        show_single(args.path, args.program)

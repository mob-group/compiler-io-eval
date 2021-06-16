import sys
import os.path
from typing import *
from dataclasses import dataclass
from enum import Enum
import reference_parser
import utilities

CCode = NewType("CCode", str)


@dataclass
class Template:
    """
    A class to produce a format string.

     Used for calls to :code:`printf` and :code:`scanf`
    """
    template: CCode
    values: Dict[str, str]
    to_fill: List[str]

    def complete(self) -> CCode:
        """
        Build the string from the template string.

        Ensures all necessary fields are filled before attempting to fill.

        :return: the filled in template
        """
        for key in self.to_fill:
            assert key in self.values

        return self.template.format(**self.values)

    def fill(self, **kwargs) -> None:
        """
        Update the Template instance with the given values.

        :param kwargs: (key, value) pairs to update the given template with
        """
        self.values.update(kwargs)


class ScalarCType(Enum):
    Int = 'int'
    Char = 'char'
    Float = 'float'
    Double = 'double'
    Bool = 'bool'

    @property
    def c_repr(self) -> CCode:
        """
        Convert a C type from object representation to the string used to denote it in C.

        :return: the C representation of the specified type
        """
        return self.value

    def placeholder(self, printf=True) -> str:
        """
        The placeholder string, as can be found in a C format string.

        Note that to make parsing easier chars are read and written from their ASCII code.

        :param printf: set to :code:`False` to get values for a :code:`scanf` format string instead
        :return: the placeholder string
        """
        if self == ScalarCType.Int:
            return "%d"
        elif self == ScalarCType.Char:
            return "%d"
        elif self == ScalarCType.Float:
            return "%f"
        elif self == ScalarCType.Double:
            return "%f" if printf else "%lf"
        elif self == ScalarCType.Bool:
            return "%d"

    @staticmethod
    def from_string(c_repr: CCode):
        """
        Find the object corresponding to the same type given a C representation of a type.

        The inverse of :code:`c_repr`

        :param c_repr: the C representation of the type
        :return: the corresponding enum vale, :code:`None` if the representation is not valid
        """
        c_type: ScalarCType
        for c_type in ScalarCType:
            if c_type.value == c_repr:
                return c_type

        return None

    def scanf_template(self) -> Template:
        """
        Build a template for calls to :code:`scanf` from a type instance.

        :return: the partially filled template
        """
        template = '''
        {c_repr} {name};
        scanf("{placeholder}", &{name});
        '''

        filled = {"c_repr": self.c_repr, "placeholder": self.placeholder(printf=False)}

        return Template(template, filled, ["name"])

    def printf_template(self) -> Template:
        """
        Build a template for calls to :code:`printf` from a type instance.

        :return: the partially filled template
        """
        template = '''
        printf("{placeholder}\\n", {name});
        '''

        return Template(template, {"placeholder": self.placeholder()}, ["name"])


@dataclass(init=False)
class VoidCType:
    def __init__(self):
        self.c_repr = "void"


@dataclass
class ArrayCType:
    scalar_c_type: ScalarCType
    size: Optional[str]  # the name of the parameter

    @property
    def c_repr(self) -> CCode:
        """
        Convert a C type from object representation to the string used to denote it in C.

        :return: the C representation of the specified type
        """
        return f"{self.scalar_c_type.c_repr}*"

    def scanf_template(self) -> Template:
        """
        Build a template for calls to :code:`scanf` from a type instance.

        :return: the partially filled template
        """
        if self.size is not None:
            template = '''
            {c_repr} {name} = malloc({size} * sizeof({scalar_c_repr}));
            
            if ({name} == NULL) {{
                fprintf(stderr, "could not alloc array {name} (size: {size}=%d)\\n", {size});
                return 1;
            }}
            
            for (int idx = 0; idx < {size}; idx++) {{
                scanf("{scalar_placeholder}", {name} + idx);
            }}
            '''
        else:
            template = '''
            {c_repr} {name} = malloc({size} * sizeof({scalar_c_repr}));
            
            if ({name} == NULL) {{
                fprintf(stderr, "could not alloc array {name} (size: {size}=%d)\\n", {size});
                return 1;
            }}

            {name}[{size}-1] = '\\0';
            for (int idx = 0; idx < {size}-1; idx++) {{
                scanf("{scalar_placeholder}", {name} + idx);
                if ({name}[idx] == '\\0')
                    break;
            }}
            '''

        filled = {"c_repr": self.c_repr,
                  "scalar_c_repr": self.scalar_c_type.c_repr,
                  "scalar_placeholder": self.scalar_c_type.placeholder(printf=False)}

        unfilled = ["name"]

        if self.size is not None:
            filled["size"] = self.size
        else:
            unfilled.append("size")

        return Template(template, filled, unfilled)

    def printf_template(self) -> Template:
        """
        Build a template for calls to :code:`scanf` from a type instance.

        :return: the partially filled template
        """
        if self.size is not None:
            template = '''
            for (int idx = 0; idx < {size}; idx++) {{
                printf("{scalar_placeholder} ", {name}[idx]);
            }}
            putchar('\\n');
            
            free({name});
            '''
        else:
            template = '''
            for (int idx = 0; {name}[idx] && idx < {size}; idx++) {{
                printf("{scalar_placeholder} ", {name}[idx]);
            }}
            putchar('\\n');
            
            free({name});
            '''

        filled = {"c_repr": self.c_repr,
                  "scalar_c_repr": self.scalar_c_type.c_repr,
                  "scalar_placeholder": self.scalar_c_type.placeholder()}

        unfilled = ["name"]

        if self.size is not None:
            filled["size"] = self.size
        else:
            unfilled.append("size")

        return Template(template, filled, unfilled)


ParameterCType = Union[ScalarCType, ArrayCType]
AnyCType = Union[ScalarCType, ArrayCType, VoidCType]


@dataclass
class CParameter:
    name: str
    c_type: ParameterCType
    is_output: bool

    @property
    def c_repr(self) -> CCode:
        """
        Convert a C parameter from object representation to the string used to denote it in C.

        :return: the C representation of the specified parameter
        """
        return f"{self.c_type.c_repr} {self.name}"

    @property
    def arr_size(self) -> CCode:
        """
        Gives the name of the variable containing the size of the array parameter.

        :return: the variable name
        """
        if isinstance(self.c_type, ScalarCType):
            raise Exception("tried to access scalar values size")

        if self.c_type.size is not None:
            return self.c_type.size
        else:
            return f"{self.name}_len"

    def get_scanf(self) -> CCode:
        """
        Get the code to create and store this parameter from stdin.

        :return: the C code
        """
        template = self.c_type.scanf_template()
        self.fill_template(template)

        return template.complete()

    def get_printf(self) -> CCode:
        """
        Get the code to write the contents of this parameter to stdout.

        :return: the C code
        """
        template = self.c_type.printf_template()
        self.fill_template(template)

        return template.complete()

    def fill_template(self, template: Template) -> None:
        """
        Add the necessary values (from :code:`template.to_fill`) to the template.

        :param template: the template to fill in
        """
        for needed in template.to_fill:
            if needed == "name":
                template.fill(name=self.name)
            elif needed == "size":
                template.fill(size=self.arr_size)


@dataclass
class CReference:
    name: str
    c_type: AnyCType
    parameters: List[CParameter]
    includes: List[str]
    code: CCode

    @staticmethod
    def parse(prog_name: str, examples_dir: str):
        """
        Build a CReference from a function directory.

        This is done using a :code:`reference_parser.FunctionReference` as an intermediate value.

        :param prog_name: the name of function directory
        :param examples_dir: the path to the directory containing the function reference
        :return: the CReference instance built from that function
        """
        inter = reference_parser.FunctionReference.parse(os.path.join(examples_dir, prog_name))
        issues = inter.validate()

        if issues:
            sys.stderr.write("Parse created issues!\n")
            sys.stderr.write("\n".join(issue.value for issue in issues))

        ignored_issues = {reference_parser.ParseIssue.ArrayReturnType}
        if issues - ignored_issues:
            raise Exception("did not produce a valid parse")

        outputs = {arr for arr in inter.info.outputs}
        params = [CParameter(param.name,
                             CReference.get_c_type(param.type),
                             param.name in outputs)
                  for param in inter.parameters]

        ref = CReference(inter.name,
                         CReference.get_c_type(inter.type),
                         params,
                         inter.reference.includes,
                         inter.code)

        for size in inter.info.sizes:
            ref.param_dict[size.array].c_type.size = size.var

        return ref

    @property
    def param_dict(self) -> Dict[str, CParameter]:
        """
        Build a dictionary mapping parameter names to the parameter objects.

        Useful for lookups.

        :return: the mapping between parameter names and the corresponding object
        """
        return {param.name: param for param in self.parameters}

    @property
    def sizes(self) -> Set[str]:
        sizes = set()
        for param in self.parameters:
            if isinstance(param, ArrayCType) and param.size is not None:
                sizes.add(param.size)

        return sizes

    @staticmethod
    def get_c_type(type_info: reference_parser.CType) -> AnyCType:
        """
        Convert an intermediate representation of a C type to the final form.

        :param type_info: the C type in its intermediate representation
        :return: the correct class matching that type
        """
        if type_info.contents == "void":
            if type_info.pointer_level != 0:
                raise Exception("void pointers are not supported")

            return VoidCType()

        c_type = ScalarCType.from_string(type_info.contents)
        assert (c_type is not None)

        if type_info.pointer_level == 0:
            return c_type
        elif type_info.pointer_level == 1:
            return ArrayCType(c_type, None)
        else:
            raise Exception("trying to create a multi-level pointer type")

    def get_func_call(self) -> CCode:
        """
        Build the call to this function, storing the result if non-void.

        :return: the C code
        """
        call = f"{self.name}({', '.join(param.name for param in self.parameters)});"

        if isinstance(self.c_type, VoidCType):
            return call

        # reserves the keyword `res'
        res = CParameter("res", self.c_type, False)

        return f"{res.c_repr} = {call}"

    @property
    def read_order(self) -> List[CParameter]:
        """
        Order the parameters so that they can be read correctly.

        An array with a given size must be read after its size.
        Since all sizes are scalar, the simplest way to achieve this is to read all scalars before arrays.

        This ordering is *stable* with respect to scalar and array parameters.
        In other words (while the order of scalar and array parameters is altered)
        the order of any scalars is not changed, nor is the order between any arrays.

        :return: the order to read parameters, with scalars coming first and arrays last
        """
        array_params = []
        scalar_params = []
        for param in self.parameters:
            if isinstance(param.c_type, ScalarCType):
                scalar_params.append(param)
            else:
                array_params.append(param)

        return scalar_params + array_params

    @property
    def outputs(self):
        return [param for param in self.parameters if param.is_output]

    def main(self) -> CCode:
        """
        Build the main function for this reference.

        This includes reading all parameters and array sizes, the call to the function, and outputting the results.

        :return: the C code
        """
        strlens = '\n'.join(self.get_strlens())
        scanfs = '\n'.join(param.get_scanf() for param in self.read_order)
        func_call = self.get_func_call()

        output_printfs = '\n'.join(param.get_printf() for param in self.parameters if param.is_output)
        if isinstance(self.c_type, VoidCType):
            printfs = output_printfs
        else:
            return_printf = self.c_type.printf_template()
            return_printf.fill(placeholder=self.c_type.placeholder(), name="res")
            return_printf = return_printf.complete()

            printfs =  return_printf + output_printfs

        return f'''
        int main(int argc, char *argv[]) {{
            {strlens}
            {scanfs}
            {func_call}
            {printfs}
        }}
        '''

    def program(self) -> CCode:
        """
        Build the whole implementation for the reference function.

        :return: the C source code
        """
        includes = "\n".join(["#include <stdio.h>", "#include <stdlib.h>"] + self.includes) + "\n"

        return f"{includes}{self.code}{self.main()}"

    def get_strlens(self) -> List[CCode]:
        """
        Build the calls to get any string parameters sizes.

        These are passed in as arguments, in the order the strings appear.
        This process reserves the identifier <arr>_len for all strings,
        where <arr> is the name of the string parameter.

        :return: the C code for getting string lengths
        """
        strlen_template = "int {arr_size} = atoi(argv[{idx}]);"
        unsized = [param.arr_size for param in self.parameters
                   if isinstance(param.c_type, ArrayCType) and param.c_type.size is None]

        return [strlen_template.format(arr_size=arr_size, idx=idx) for idx, arr_size in enumerate(unsized, start=1)]

    def compile(self, exe: str = None, cleanup: bool = True) -> Optional[str]:
        """
        Compile the function.

        :param exe: the name of the executable to compile to. If :code:`None` then a random string will be used
        :param cleanup: set to :code:`True` to remove the source file afterwards
        :return: the name of the executable. Returns :code:`None` if compilation failed
        """
        if exe is None:
            exe = utilities.get_tmp_file_name(self.program(), ".o")

        root, ext = os.path.splitext(exe)

        if ext not in {".o", ""}:
            raise Exception("invalid executable given")

        src = root+".c"

        if os.path.exists(exe):
            ack = input(f"overwrite {exe}/{src}? [yN]\n")
            if ack == "y" or ack == "Y":
                print("overwriting...")
            else:
                print("not overwriting!")
                return None

        with open(src, "w") as f:
            f.write(self.program())

        _, stderr = utilities.run_command(f"gcc -Wall -O0 -o {exe} {src}")
        if stderr:
            sys.stderr.write(stderr)
        print("COMPILED")

        if cleanup:
            utilities.run_command(f"rm {src}")

        return exe


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser()

    parser.add_argument("program", help="the program to use")
    parser.add_argument("-p", "--path", help="path to the directory contatining the program", default=".")
    parser.add_argument("-c", "--compile", help="pass to compile the function", action="store_true")

    args = parser.parse_args()

    parsed = CReference.parse(args.program, args.path)
    if args.compile:
        parsed.compile(exe="tmp.o", cleanup=False)
    else:
        print(parsed.program())

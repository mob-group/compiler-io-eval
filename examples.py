from re import match
from reference_parser import CParameter, CType
from dataclasses import dataclass
from typing import *

# turn back now, regex grossness ahead
# if this breaks somewhere down the line just replace it with a JSON reader or something

Example = Tuple[list, Any, list]


@dataclass
class ExampleCollection:
    """
    Contains a list of examples from an examples file
    """
    inputs: List[CParameter]
    ret_type: CType
    outputs: List[CParameter]
    examples: List[Example]

    @staticmethod
    def parse(sig: str, examples: List[str]):
        """
        Uses a signature string and a list of examples to build the collection

        The signature string is composed of three sections:

            (<inputs>) <return> (<outputs>)

        where <inputs> and <outputs> are a comma-separated list of parameters, and <ret> is a type.

        The examples are of a similar form, except instead of parameters the fields contain
        the values of the corresponding parameter.
        For a void return type the special value '_' is used.

        Example file:
            (int a, float b, char *s) void (char *s)
            (1, 1.5, "a string") _ (" a new string")
            (-4, 10.001, "a string with \" escaped characters") _ ("less chars")
            ...

        Another example file:
            (int *a, int *b, int n) int ()
            ([1, 2, 3], [4, 5, 6], 3) -1 ()
            ([10. 15, 20, 25, 30], [1, -2, 3, -4, 5], 5) 10 ()
            ...

        Any examples that can not be parsed correctly are ignored.

        :param sig: the signature string describing the examples
        :param examples: a list of example strings
        :return: the ExampleCollection built from this selection
        """
        inputs, ret_type, outputs = ExampleCollection.parse_sig(sig)
        input_parsers = [ExampleCollection.parser_for(inp.type) for inp in inputs]
        ret_parser = ExampleCollection.parser_for(ret_type)
        output_parsers = [ExampleCollection.parser_for(outp.type) for outp in outputs]

        vals = []
        for example in examples:
            val = ExampleCollection.parse_example(example, input_parsers, ret_parser, output_parsers)
            if val is not None:
                vals.append(val)

        return ExampleCollection(inputs, ret_type, outputs, vals)

    @staticmethod
    def from_file(example_file: str):
        """
        Builds an ExampleCollection from a file.

        Reads a file and extracts the signature and examples for a call to parse.
        See parse for more information.

        :param example_file: the file containing the examples
        :return: the ExampleCollection built from that file
        """
        with open(example_file, "r") as examples:
            sig = examples.readline()
            return ExampleCollection.parse(sig, examples.readlines())

    @staticmethod
    def parse_int(s: str) -> (int, str):
        if (m := match(r"\s*(-?\d+)", s)) is not None:
            return int(m[1]), s[m.end():]
        else:
            return None

    @staticmethod
    def parse_real(s: str) -> (float, str):
        if (m := match(r"\s*(-?\d+(?:\.\d+)?)", s)) is not None:
            return float(m[1]), s[m.end():]
        else:
            raise None

    @staticmethod
    def parse_char(s: str) -> (str, str):
        if (m := match(r"\s*'([^\\']|\\.)'", s)) is not None:
            return m[1], s[m.end():]
        else:
            return None

    @staticmethod
    def parse_bool(s: str) -> (bool, str):
        if (m := match(r"\s*(True|False)", s)) is not None:
            return m[1] == "True", s[m.end():]
        else:
            return None

    @staticmethod
    def parse_string(s: str) -> (str, str):
        if (m := match(r'\s*"((?:[^\\"]|\\.)*)"', s)) is not None:
            return m[1], s[m.end():]
        else:
            return None

    @staticmethod
    def parse_list(s: str, elem) -> (list, str):
        if (m := match(r"\s*\[", s)) is None:
            return None

        res = []
        rem = s[m.end():]
        while (inner_m := elem(rem)) is not None:
            v, rem = inner_m
            res.append(v)

            if (sep := match(r"\s*,", rem)) is None:
                break

            rem = rem[sep.end():]

        if (m := match(r"\s*]", rem)) is not None:
            return res, rem[m.end():]
        else:
            return None

    @staticmethod
    def parse_missing(s: str) -> (None, str):
        """
        A special parser meant to parse the void value '_'

        :param s: the string to parse
        :return: a tuple, shifting the input string correctly, if parsing occurred otherwise :code:`None`
        """
        if (m := match(r"\s*_", s)) is not None:
            return None, s[m.end():]
        else:
            return None

    @staticmethod
    def parser_for(c_type: CType) -> Callable:
        """
        Fetch the correct parser for a given type

        Recursively wraps pointers in lists if necessary.

        :param c_type: the type to parse
        :return: a function taking a string as input and returning a the parse of the string for the given type
        """
        if c_type.contents == "void":
            return ExampleCollection.parse_missing

        if c_type == CType("char", 1):
            return ExampleCollection.parse_string

        if c_type.pointer_level >= 1:
            inner_parser = ExampleCollection.parser_for(CType(c_type.contents, c_type.pointer_level - 1))
            return lambda s: ExampleCollection.parse_list(s, inner_parser)

        if c_type.contents == "int":
            return ExampleCollection.parse_int
        elif c_type.contents == "float" or c_type.contents == "double":
            return ExampleCollection.parse_real
        elif c_type.contents == "char":
            return ExampleCollection.parse_char
        elif c_type.contents == "bool":
            return ExampleCollection.parse_bool
        else:
            raise Exception(f"no parser exists for type: {c_type}")

    @staticmethod
    def parse_sig(s: str):
        """
        Parses a signature string into the corresponding parameters/types

        :param s: the string to parse
        :return: a tuple of the form ([inputs], return, [outputs])
        """
        inp_end = s.index(")") + 1
        outp_start = s.rindex("(")

        assert inp_end < outp_start

        inps = s[:inp_end].strip()
        ret = s[inp_end:outp_start].strip()
        outps = s[outp_start:].strip()

        assert inps[0] == outps[0] == "("
        assert inps[-1] == outps[-1] == ")"

        input_params = [CParameter.parse(param.strip()) for param in inps[1:-1].split(',') if param.strip()]
        ret_type = CType.parse(ret)
        output_params = [CParameter.parse(param.strip()) for param in outps[1:-1].split(',') if param.strip()]

        return input_params, ret_type, output_params

    @staticmethod
    def parse_example(s: str, inps: List[Callable], ret: Callable, outps: List[Callable]) -> Optional[Example]:
        """
        Parses an example

        Details of the example format can be found in the :code:`parse` method.

        :param s: the string to parse
        :param inps: parsers for the input values
        :param ret: a parser for the return value
        :param outps: parsers for the output values
        :return: the example that has beem parsed. Returns :code:`None` if this example could not be parsed
        """

        def parse_group(s: str, grp: List[Callable]) -> Optional[Tuple[list, str]]:
            """
            Helper function to parse something of the form:

                (<values>)

            where <values> is a comma-separated list of values that can be parsed by the parsers in :code:`grp`.

            :param s: the string to parse
            :param grp: the parsers to use to parse this group
            :return: a standard parse result; the values and the new string position if successful
            or :code:`None` if not
            """
            s = s[s.index("(") + 1:]

            grp_vals = []
            for parser in grp:
                if (parsed := parser(s)) is None:
                    return None

                val, s = parsed
                grp_vals.append(val)

                if (m := match(r"\s*,", s)) is not None:
                    s = s[m.end():]

            if (m := match(r"\s*\)", s)) is not None:
                s = s[m.end():]
            else:
                return None

            return grp_vals, s

        if (parsed := parse_group(s, inps)) is None:
            return None
        input_vals, s = parsed

        if (parsed := ret(s)) is None:
            return None
        ret_val, s = parsed

        if (parsed := parse_group(s, outps)) is None:
            return None
        output_vals, s = parsed

        return input_vals, ret_val, output_vals


if __name__ == '__main__':
    ExampleCollection.parser_for(CType("int", 1))
    ec = ExampleCollection.parse("(int *a, int *b, int n) int ()",
                                 [
                                     "([1, 2, 3], [4, 5, 6], 3) -1 ()",
                                     "([10, 15, 20, 25, 30], [1, -2, 3, -4, 5], 5) 10 ()",
                                 ])

    print(ec)

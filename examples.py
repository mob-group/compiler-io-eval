from dataclasses import dataclass
from typing import *
from re import match
from runner import SomeValue, AnyValue
from reference_parser import CType, CParameter, FunctionReference

Name = NewType("Name", str)
Parser = NewType("Parser", Callable)

ParameterMapping = dict[Name, SomeValue]
TypeMapping = list[tuple[Name, CType]]
ParserMapping = list[tuple[Name, Parser]]

base_str = "({inputs}) {value} ({outputs})"


@dataclass
class ExampleInstance:
    inputs: ParameterMapping
    value: AnyValue
    outputs: ParameterMapping

    def generate(self, inputs: TypeMapping, value: CType, outputs: TypeMapping) -> str:
        inp_vals = [generate_value(self.inputs[name], c_type)
                    for name, c_type in inputs]
        outp_vals = [generate_value(self.outputs[name], c_type)
                     for name, c_type in outputs]

        return base_str.format(inputs=", ".join(inp_vals),
                               value=generate_value(self.value, value),
                               outputs=", ".join(outp_vals))

    @staticmethod
    def parse(inputs: ParserMapping, value: Parser, outputs: ParserMapping, s: str):
        """
            Parses an example

            Details of the example format can be found in the :code:`parse` method.

            :param s: the string to parse
            :param inputs: parsers for the input values
            :param value: a parser for the return value
            :param outputs: parsers for the output values
            :return: the example that has been parsed. Returns :code:`None` if this example could not be parsed
            """

        def parse_group(s: str, grp: ParserMapping) -> Optional[tuple[ParameterMapping, str]]:
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

            grp_vals = {}
            for name, parser in grp:
                if (parsed := parser(s)) is None:
                    return None

                val, s = parsed
                grp_vals[name] = val

                if (m := match(r"\s*,", s)) is not None:
                    s = s[m.end():]

            if (m := match(r"\s*\)", s)) is not None:
                s = s[m.end():]
            else:
                return None

            return grp_vals, s

        if (parsed := parse_group(s, inputs)) is None:
            return None
        input_vals, s = parsed

        if (parsed := value(s)) is None:
            return None
        ret_val, s = parsed

        if (parsed := parse_group(s, outputs)) is None:
            return None
        output_vals, s = parsed

        return ExampleInstance(input_vals, ret_val, output_vals)


def generate(reference: FunctionReference, examples: list[ExampleInstance]) -> list[str]:
    inputs = [(param.name, param.type) for param in reference.parameters()]
    value = reference.type
    outputs = [(param.name, param.type) for param in reference.outputs()]

    return generate_examples(inputs, value, outputs, examples)


def generate_examples(inputs: TypeMapping, value: CType, outputs: TypeMapping,
                      examples: list[ExampleInstance]) -> list[str]:
    sig_str = [generate_ref(inputs, value, outputs)]
    example_strs = [ex.generate(inputs, value, outputs) for ex in examples]

    return sig_str + example_strs


def generate_ref(inputs: TypeMapping, value: CType, outputs: TypeMapping) -> str:
    inps = [str(CParameter(name, c_type)) for name, c_type in inputs]
    outps = [str(CParameter(name, c_type)) for name, c_type in outputs]

    return base_str.format(inputs=", ".join(inps),
                           value=str(value),
                           outputs=", ".join(outps))


def generate_value(val: AnyValue, c_type: CType) -> str:
    if c_type == CType("char", 1):
        return f'"{val}"'
    elif c_type == CType("void", 0):
        return "_"

    return str(val)


def parse(sig: str, examples: list[str]) -> list[ExampleInstance]:
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

    :param sig:
    :param examples:
    :return:
    """
    inputs, value, outputs = parse_sig(sig)

    return parse_examples(inputs, value, outputs, examples)


def parse_sig(s: str):
    """
    Parses a signature string into the corresponding parameters/types

    :param s: the string to parse
    :return: a tuple containing the parsers for (inputs, return value, outputs)
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

    inputs = [(param.name, parser_for(param.type)) for param in input_params]
    value = parser_for(ret_type)
    outputs = [(param.name, parser_for(param.type)) for param in output_params]

    return inputs, value, outputs


def parse_examples(inputs: ParserMapping, value: Parser, outputs: ParserMapping,
                   examples: list[str]) -> list[ExampleInstance]:
    """
    Parses a collection of examples using the given parsers.

    :param inputs: 
    :param value: 
    :param outputs: 
    :param examples: 
    :return: the ExampleInstance built from this selection
    """

    vals = []
    for example in examples:
        val = ExampleInstance.parse(inputs, value, outputs, example)
        if val is not None:
            vals.append(val)

    return vals


def parse_int(s: str) -> (int, str):
    if (m := match(r"\s*(-?\d+)", s)) is not None:
        return int(m[1]), s[m.end():]
    else:
        return None


def parse_real(s: str) -> (float, str):
    if (m := match(r"\s*(-?\d+(?:\.\d+)?)", s)) is not None:
        return float(m[1]), s[m.end():]
    else:
        raise None


def parse_char(s: str) -> (str, str):
    if (m := match(r"\s*'([^\\']|\\.)'", s)) is not None:
        return m[1], s[m.end():]
    else:
        return None


def parse_bool(s: str) -> (bool, str):
    if (m := match(r"\s*(True|False)", s)) is not None:
        return m[1] == "True", s[m.end():]
    else:
        return None


def parse_string(s: str) -> (str, str):
    if (m := match(r'\s*"((?:[^\\"]|\\.)*)"', s)) is not None:
        return m[1], s[m.end():]
    else:
        return None


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


def parser_for(c_type: CType) -> Parser:
    """
    Fetch the correct parser for a given type

    Recursively wraps pointers in lists if necessary.

    :param c_type: the type to parse
    :return: a function taking a string as input and returning a the parse of the string for the given type
    """
    if c_type.contents == "void":
        return parse_missing

    if c_type == CType("char", 1):
        return parse_string

    if c_type.pointer_level >= 1:
        inner_parser = parser_for(CType(c_type.contents, c_type.pointer_level - 1))
        return lambda s: parse_list(s, inner_parser)

    if c_type.contents == "int":
        return parse_int
    elif c_type.contents == "float" or c_type.contents == "double":
        return parse_real
    elif c_type.contents == "char":
        return parse_char
    elif c_type.contents == "bool":
        return parse_bool
    else:
        raise Exception(f"no parser exists for type: {c_type}")

if __name__ == '__main__':
    stuff = parse("(char *s, char c, int a) int (char *s)", ['("hello", "o", 3) 10 ("wow")', '("ooh", \'p\', 1) 13 ("wow")', '("very cool", \'!\', 15) 85 ("wow")',])
    stuff


import os.path
from typing import *
from dataclasses import dataclass
from reference_parser import FunctionReference, CType, CParameter
from runner import Function, SomeValue, AnyValue
from randomiser import Randomiser

ParameterMapping = dict[str, SomeValue]


@dataclass
class ExampleInstance:
    inputs: ParameterMapping
    value: SomeValue
    outputs: ParameterMapping

    def __str__(self):
        def mapping_str(mapping: Tuple[str, SomeValue]):
            name, val = mapping
            return f"{name} -> {val}"

        s = ["==== INPUTS ===="]
        for inp in self.inputs.items():
            s.append(mapping_str(inp))

        s += ["==== RETURN ===="]
        s.append(f"-> {self.value}")

        s += ["==== OUTPUTS ==="]
        for outp in self.outputs.items():
            s.append(mapping_str(outp))

        s += ["================"]

        return "\n".join(s)


class Generator:
    def __init__(self, reference: str, path: str):
        reference_path = os.path.join(path, reference)
        self.reference = FunctionReference.parse(reference_path)
        self.runner = Function(reference, path)

        random_seed = 0  # change to `None' outside of testing
        self.randomiser = Randomiser(seed=random_seed)

    def format(self, examples: list[ExampleInstance]) -> list[str]:
        base_str = "({inputs}) {value} ({outputs})"

        def format_ref() -> str:
            inps = [str(param) for param in self.reference.parameters()]
            outps = [str(param) for param in self.reference.outputs()]

            return base_str.format(inputs=", ".join(inps),
                                   value=str(self.reference.type),
                                   outputs=", ".join(outps))

        def format_example(example: ExampleInstance) -> str:
            inp_vals = [format_value(example.inputs[param.name], param.type)
                        for param in self.reference.parameters()]
            outp_vals = [format_value(example.outputs[param.name], param.type)
                         for param in self.reference.outputs()]

            return base_str.format(inputs=", ".join(inp_vals),
                                   value=format_value(example.value, self.reference.type),
                                   outputs=", ".join(outp_vals))

        def format_value(val: AnyValue, c_type: CType) -> str:
            if c_type == CType("char", 1):
                return f'"{val}"'
            elif c_type == CType("void", 0):
                return "_"

            return str(val)

        sig_str = [format_ref()]
        example_strs = [format_example(ex) for ex in examples]

        return sig_str + example_strs

    def generate(self, n: int) -> list[ExampleInstance]:
        assert n > 0

        return [self.generate_single() for _ in range(n)]

    def generate_single(self) -> ExampleInstance:
        inputs = {}
        for param in self.reference.parameters(safe_order=True):
            self.random(param, inputs)

        value = self.runner.run(**inputs)

        outputs = self.runner.outputs()

        return ExampleInstance(inputs, value, outputs)

    def random(self, parameter: CParameter, current: ParameterMapping):
        assert 0 <= parameter.type.pointer_level < 2

        scalar_type = parameter.type.contents
        # add in any range changes here
        # NOTE: that's why they're all lambdas
        if scalar_type == "int":
            gen = lambda: self.randomiser.random_int()
        elif scalar_type == "float":
            gen = lambda: self.randomiser.random_float()
        elif scalar_type == "double":
            gen = lambda: self.randomiser.random_double()
        elif scalar_type == "char":
            gen = lambda: self.randomiser.random_char()
        elif scalar_type == "bool":
            gen = lambda: self.randomiser.random_bool()
        else:
            raise Exception(f"can't produce value of type {scalar_type}")

        if parameter.type.pointer_level == 0:
            val = gen()
        elif parameter.type.contents == "char":
            max_str_len = 100
            val = self.randomiser.random_string(max_str_len)
        else:
            size = self.reference.info.size(parameter)

            if size not in current:
                raise Exception(f"could not find size ({size}) for parameter ({parameter.name})")
            else:
                size = current[size]

            val = self.randomiser.random_array(size, gen)

        current[parameter.name] = val
        return val


class Evaluator:
    pass


if __name__ == '__main__':
    path = "/Users/sami/Documents/haxx/internship/synthesis-eval/examples/"
    prog = "str_chr"

    g = Generator(prog, path)
    print("\n".join(g.format(g.generate(2))))

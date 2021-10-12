# compiler-io-eval

This is a tool to benchmark compilers correctness, by testing a compiled functions output on a given set of I/O pairs to
the function. This library provides a method of generating these pairs using [GCC](https://gcc.gnu.org/). A function can
then be checked against these I/O pairs, to see if the compiled version produces the correct output.

## requirements

This project support Python 3.9 only.
It depends on [synthesis-eval](https://github.com/mob-group/synthesis-eval) for an example set.

## usage

The main script can be found in "eval.py", which can be used as follows.

     python3 eval.py REFERENCES IMPLEMENTATIONS

Here `REFERENCES` is the directory containing all reference directories,
and `IMPLEMENTATIONS` is the directory contatining all imlementation directories.
As an example the script would be called as `python3 eval.py refs impls` if the following was the directory structure.

     .
     +-- refs/
     |   +-- a/
     |   |   +-- ref.c
     |   |   +-- props
     |   +-- b/
     |   |   +-- ref.c
     |   |   +-- props
     +-- impla/
     |   +-- a/
     |   |   +-- a-1.s
     |   |   +-- a-2.s
     |   |   +-- a-3.s
     |   +-- b/
     |   |   +-- b-1.s
     |   |   +-- b-2.s
     |   |   +-- b-3.s
     |   |   +-- b-4.s

The output of the benchmarks are written as a report to stdout,
and any issues encountered in the process are written to "benchmarking.log".

## components

The library is modular, so it is made up of a few different components.

- reference_parser.py
- runner.py
- examples.py
- randomiser.py
- evaluation.py
- helper_types.py
- lumberjack.py

*`reference_parser` can be used as a command line tool*

### reference_parser

This component extracts useful information from a reference, and can check the validity of a reference. Note, a
reference is a directory containing a "ref.c" and "props" file.

As a module this exposes the convenience function `load_reference`
which provides a simpler interface for parsing a reference from a directory.

As a command line tool it provides functionality to check a single or all references in a given location. Usage is as
follows:

    python3 reference_parser.py [-p PATH] [-a | PROGRAM]

Here `PATH` denotes the location of the reference functions, and it defaults to the current directory.

If the flag `-a` is given then all references in `PATH` are checked. This outputs the signature of all the functions
to **stdout**, useful for testing, and outputs any issues with the references to **stderr**.

If instead `PROGRAM` is given then that denotes the single reference to check. This will output a JSON representation of
the reference if it is valid, otherwise it will write any issues to **stderr**.

#### examples

In the following directory structure, where `*` denotes the current directory:

    .
    +-- src *
    |   +-- reference_parser.py
    +-- examples
    |   +-- a
    |   |   +-- ref.c
    |   |   +-- props
    |   +-- b
    |   |   +-- ref.c
    |   |   +-- props

Then you could run:

    $ python3 reference_parser.py -p /examples a
    $ python3 reference_parser.py -p /examples b

to produce output for `a`/`b` individually.

You could also run:

    $ python3 reference_parser.py -p /examples -a

which would produce a simplified output for both `a` and `b`.

### runner.py

This component contains the functionality for executing a reference function. It uses the `ctypes` library, which is
able to run [shared objects](https://tldp.org/HOWTO/Program-Library-HOWTO/shared-libraries.html). All conversions
between native and foreign types are handled here, so the use of the library is opaque to the programmer.

The method `create` is also exposed; this is a helper to set up an executable reference function.

#### sand-boxing

The implementations being tested may have problems, some of these will be fatal;
if they were run directly by the benchmarker then any crashes would also cause the benchmark to crash,
leaving all the following implementations untested.
To prevent this from happening whenever the function is called a new process is spawned.
The implementation runs in this process, and its outputs are extracted into the parent process (the benchmark).
The function `run_safe` is exposed to do this.

### examples.py

This component handles **examples**, which in this use case are the I/O pairs for a reference. It exposes
an `ExampleInstance` class to structure these, as well as the functions `parse` (which maps a string to
an `ExampleInstance`)
and `form` (which maps an `ExampleInstance` to a string). These two functions are the inverse of each other.

The format for serialising an example is as follows:

    EXAMPLE ::= "(" INPUTS ")" RETURN "(" OUTPUTS ")"
    INPUTS  ::= VALUES
    RETURN  ::= "_" | VALUE
    OUTPUTS ::= EMPTY | VALUES
    VALUES  ::= VALUE | VALUE "," VALUES
    VALUE   ::= SCALAR | ARRAY
    SCALAR  ::= INT | FLOAT | CHAR | BOOL
    INT     ::= <any decimal integer>
    FLOAT   ::= <any decimal floating point number>
    CHAR    ::= "'" <a single character> "'"
    BOOL    ::= True | False
    ARRAY   ::= '"' <a string of characters> '"' | "[" (SCALAR ",")+ SCALAR "]" | "[]"

### randomiser.py

This component handles random element generation. It exposes the `Randomiser` class, which has a method to generate a
random value for each of the supported types.

### evaluation.py

This component contains the functionality to generate and evaluate examples for a reference.

This component provides functions that do the main tasks, `generate`, `write_examples`, and `evaluate`.
As the name suggests `generate` is used to generate examples, given a reference and an implementation to use;
these examples are produced in the method described in [generation](#example-generation).
`write_examples` can then be used to store these in a file if necessary.
Again, `evaluate` does as it says, and evaluates a set of examples on a particular implementation.

### helper_types.py

To avoid circular dependencies all common data is extracted into a separate file.

`helper_types` exposes the types `ScalarValue`, `ArrayValue`, `SomeValue`, `AnyValue`. These are used to denote the native
Python versions of C types supported in this library. The meanings of scalar and array values should be obvious, except
note that as Python lacks a `char` type both this and `char *` are defined as Python `str`'s.
`SomeValue` is either a scalar or array value, and `AnyValue` differs from `SomeValue` only because it can be `None`
/`void`.

It also exposes the aliases `Name` and `Parser` which are just used to clarify the purpose of a value in certain places.
Similarly the type `ParameterMapping` is commonly used to store parameter names and their values in a dictionary.

There are also many errors defined in `helper_types`. These are used throughout,
and depending on what they are used for they may be either a simple subclass of `Exception`,
or actually provide some functionality.

### lumberjack.py 

`lumberjack` is a simple interface to `logging`.
This is used to ensure all loggers are set up correctly, whichever module is used.

# notes

There are a few tricky concepts in this project, so what follows will be more detail on the most important of those.

## benchmarking a function

Benchmarking a function involves a lot of phases, spread out across the different components,
and understanding them is key to understanding the code at all.

The flow for benchmarking a single function contained in an implementation file,
with a corresponding reference directory uses the following process:

  1. parse reference: `<reference> -> ref`
  2. build reference function: `ref, <reference/ref.c> -> run`
  3. build generator: `ref, run -> generator`
  4. generate examples: `generator -> examples`
  5. build real function: `ref, <implementation> -> run'`
  6. build evaluator: `ref, run' -> evaluator`
  7. check examples: `evaluator, examples -> results`

Note that this is the idealised flow,
due to [sand-boxing](#sand-boxing) the actual flow looks slightly different (and less obvious).
This means building a function (creating a `run`) is typically delayed until the function has to be run,
and then a new function will be created specifically for that run.
This is true for both generation and evaluation.

## benchmarking a collection of functions

The process for running the benchmark on many references and implementations can also be thought of in phases.
These steps look distinct from those laid out for [a single function](#benchmarking-a-function);
that procedure is still taking place here, only beneath the surface.

To benchmark many functions this library will:

   1. retrieve - fetches all folders and files which could be testable
      1. get all reference folders which contain "ref.c" and "props"
      2. get all implementation files from the folder with the same name which end with a valid extension
   2. build - iterates over all folders and files found and converts them to usable objects
      1. turn each folder into a `FunctionReference`, skip this whole reference if it fails
      2. turn each implementation for that reference into a `Function`, also skip if it fails
   3. test - tests each reference and store all the results
      1. build a `Generator` and generate examples for each reference
      2. build an `Evaluator` for each implementation and test on the examples

### skips

Knowing when references and implementations are skipped is tricky, so here is an example flow covering most cases.
It will show which entries are being considered after each pass, given the starting directories:

    .
    +-- refs/
    |   +-- a/
    |   |   +-- ref.c
    |   |   +-- props
    |   +-- b/
    |   |   +-- ref.c
    |   |   +-- props
    |   +-- c/
    |   |   +-- ref.c
    |   |   +-- props
    |   +-- d/
    |   |   +-- ref.c [invalid]
    |   |   +-- props
    |   +-- e/
    |   |   +-- props
    |   +-- f/
    |   |   +-- README.md
    |   +-- g.txt
    |-- impls
    |   +-- a/
    |   |   +-- a-1.s
    |   |   +-- a-2.s [invalid]
    |   |   +-- a-3.s [always segfaults]
    |   +-- b/
    |   |   +-- b-1.s [invalid]
    |   |   +-- b-2.s [invalid]
    |   +-- c/
    |   +-- d/
    |   |   +-- d-1.s
    |   |   +-- d-2.s
    |   +-- e/
    |   |   +-- e-1.s
    |   |   +-- e-2.s
    |   +-- f/
    |   |   +-- f-1.s
    |   |   +-- f-2.s

**after fetching:**

    a/ [a-1.s, a-2.s, a-3.s]
    b/ [b-1.s, b-2.s]
    d/ [d-1.s, d-2.s]

**after building:**

    a/ [a-1.s, a-3.s]

**final results:**

     PASSES 1/2: a-1.s 50/50 OK
     FAILS: 0/2:
     TRIVIAL 1/2: a-2.s 0/0 OK

## example generation

This library provides an ability to generate examples for a reference function. The method for achieving this is simple
but still powerful, although it does come with drawbacks. Examples are generated as follows:

1. parse the function reference to find out what the I/O specification is
2. build an executable version of the function (see [executing functions](#function-execution))
3. randomly generate an input tuple
4. run the function on the random input
5. extract the return value and output parameters of that function call
6. build an example from that input/output evaluation
7. if more examples are required repeat from step 3

In this way many examples can be produced easily. The only dependency of this method is that building the executable in
step 2 produces a correct function, I have used GCC as my "ground truth". Therefore, as long as functions compiled with
GCC produce the correct output, then examples generated in this way will have the correct result.

This approach is also agnostic to how the inputs are generated, this means that a different technique could be used to
sample the input space to test the function more efficiently.

## function execution

In order to run a reference function, which does not necessarily exist in a valid stand-alone C file, this library
interacts with functions as shared objects. Compilation into this format varies between Linux and macOS and this library
makes no effort to support multiple platforms, so if this is intended for use on a different system take care. The whole
shared object infrastructure seems different for Windows, so even more work may be required to run this library there.

The [ctypes](https://docs.python.org/3/library/ctypes.html) library is used to interface with these shared objects, so
for more information on the internals visit their site.

The approach this library uses is to compile and extract a function definition from a reference. At this point it is a
callable Python object, which will acts as an interface to the C function. Unfortunately by default the function object
has very little information about the function, so the next step is to provide the function with type information.
"Type information" in this case means registering a class which can transform native Python types into an object
that `ctypes` can work with. For scalar types this is simple, however as array types include their sizes these must be
generated dynamically as values are passed to the function. As these `ctypes` objects are created dynamically they have
to be registered somewhere if you would like to check their value outside the function call, as is the case with output
parameters. In summary, the procedure for calling a reference function from Python is:

1. parse the reference to find out I/O types
2. compile the implementation to a shared object and load as a Python object
3. generate and attach the input and return types to the function object
4. call the function with input arguments
    1. dynamically convert these native Python arguments to their `ctypes` counterpart
    2. keep track of these `ctypes` objects if their value is required later
5. extract the return value and output values after the function call
    
## props files

Each function reference has certain properties, in a "props" file, which are used in various ways throughout the code.
The grammar for this "props" file is given below. This clarifies what is allowed in a "props" file but,
as productions map nicely onto classes, it also gives context for certain types.

    PROPS      ::= <signature> INFO
    INFO       ::= OUTPUT | SIZE | CONSTRAINT
    OUTPUT     ::= "output" <var_name>
    SIZE       ::= "size" <var_name> "," <var_name>
                 | "size" <var_name> "," <integer>
                 | "size" <var_name> "," "{" <expr> "}"
                 | "size" <var_name> "," <integer> "," "{" <expr> "}"
    CONSTRAINT ::= "constraint" <var_name> OP <any value>
                 | "constraint" "{" <expr> "}"
    OP         ::= "==" | "!=" | "<" | "<=" | ">" | ">="

As mentioned above certain classes correspond precisely to these productions:

  - `ParamSize` objects are constructed from SIZE productions
    - `VarSize`, `ConstSize`, `SimpleExprSize`, and `ExprSize` objects come from each branch in that production
  - `Constraint` objects are constructed from CONSTRAINT productions
    - `ParamConstraint`, and `GlobalConstraint` objects match each branch respectively too

# issues

current known issues are:

   - process hangs after finishing
      - can CTRL-C if used manually
      - could wrap it in a timeout shell script?
   - parsing for expressions is not perfect, fails if a comma appears in a simple expression
      - could use a proper parser generator (the grammar is already written)
      - can change the current parser to handle those expressions better
   -

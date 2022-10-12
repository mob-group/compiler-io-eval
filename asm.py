import sh
from dataclasses import dataclass, asdict
from abc import ABC
from koda import Ok, Err, Result
import re

# For the moment, don't support MASM

def get_text_size(binary_path):
    try:
        outasm = sh.size(binary_path)
        lines = [l.split() for l in outasm.split('\n')]
        if lines[0][0]=='text':
           return int(lines[1][0])
    except BaseException as e:
        print(e)
    return None

@dataclass
class AsmTarget:
    impl: str
    bits: int
    lang: str
    o: str

    def __post_init__(self):
        assert self.impl in ['gcc', 'clang']
        assert self.bits in [32, 64]
        assert self.lang in ['masm', 'gas', 'llvm']
        assert self.o in ['0', '1', '2', '3', 'fast', 'g', 'fast', 's', 'z']

    def dict(self):
        return asdict(self)


@dataclass
class FuncAsm:
    pre_asm: str  # asm directives before, and also e.g. global variable declarations needed to compile llvm functions
    func_asm: str  # asm of function itself
    post_asm: str  # asm directives after the function itself
    target: AsmTarget

    def dict(self):
        return asdict(self)


class Compiler:
    def __init__(self, arch, o, lang, bits=64, flags=[]):
        self.arch = arch
        self.o = o
        self.bits = bits
        self.lang = lang
        self.flags = flags

    def get_func_asm(self, all_required_c_code, fname, output_path=None) -> Result[FuncAsm, BaseException]:
        return self._get_func_asm(all_required_c_code, fname, output_path, arch=self.arch, o=self.o, bits=self.bits, flags=self.flags)

    def _get_func_asm(self, all_required_c_code, fname, output_path, arch, o, bits, flags) -> Result[FuncAsm, BaseException]:
        raise NotImplementedError

    def get_text_size(self, all_required_c_code, fname, output_path=None) -> Result[FuncAsm, BaseException]:
        return self._get_text_size(all_required_c_code, fname, output_path, arch=self.arch, o=self.o, bits=self.bits, flags=self.flags)

    def _get_text_size(self, all_required_c_code, fname, output_path, arch, o, bits, flags) -> Result[FuncAsm, BaseException]:
        raise NotImplementedError


    def _asm_replace_constants_with_literals(self, all_asm, func_asm):
        raise NotImplementedError

    @classmethod
    def factory(cls, impl, *args, **kwargs):
        if impl == 'gcc':
            return GCC(*args, **kwargs)
        elif impl == 'clang':
            return Clang(*args, **kwargs)
        raise NotImplementedError(f'impl = {impl}')


class GASCompiler(ABC, Compiler):

    def get_comment_sym(self):
        if self.lang == 'gas':
            if self.arch == 'arm':
                return '@'
            return '#'
        elif self.lang == 'llvm':
            return ';'
        else:
            raise ValueError(f'lang = {self.lang}')

    def _asm_replace_constants_with_literals(self, all_asm, func_asm):
        all_asm = all_asm.decode("utf-8")
        asm_to_add = []
        for symbol in re.compile('\.LC[0-9]*').findall(func_asm):  # TODO: move, compile once
            for e in re.findall(f'\.{symbol.replace(".", "")}:[\r\n]+([^\r\n]+)', all_asm):
                asm_to_add.append(symbol + ': ' + e)
                break
        for symbol in re.compile('a\.[0-9]*').findall(func_asm):  # TODO: move, compile once
            for e in re.findall(f'{symbol}:[\r\n]+([^\r\n]+)', all_asm):
                asm_to_add.append(symbol + ': ' + e)
                break
        return func_asm + '\n' + '\n'.join(asm_to_add) + '\n'

    def _gas_get_func_asm_from_all_asm(self, fname, all_asm):
        def strip_comments(code, comment_sym):  # only support simple commands, asm
            res = []
            for l in code.splitlines():
                without_comments = l.split(comment_sym)[0]
                if len(without_comments.split()) > 0:
                    res.append(without_comments)
            return '\n'.join(res)

        #print('fname:',fname)
        #func = [f'.globl {fname}', f'.type {fname}, @function']
        asm_fname = None
        inside_func = False
        after_func = False
        func = []
        pre_asm = []
        post_asm = []
        for l in all_asm.splitlines():
            tokens = l.split()
            if inside_func:
                func.append(l)
            elif after_func:
                post_asm.append(l)
            else:
                if len(pre_asm)>0:
                    pre_asm.append(l)
                if len(tokens)>2 and tokens[0]=='.globl':
                    pre_asm.append(l)
                    asm_fname = tokens[1]
                tmp = re.split(' |\t|,', l)
                if asm_fname in tmp and '@function' in tmp:
                    inside_func = True
            if inside_func and '.cfi_endproc' in tokens:
                inside_func = False
                after_func = True

        pre_asm = '\n'.join(pre_asm) + '\n'
        func_asm = '\n'.join([f'.globl {asm_fname}', f'.type {asm_fname}, @function'])+'\n'
        func_asm += '\n'.join(func) + '\n'
        comment_sym = self.get_comment_sym()
        func_asm = strip_comments(func_asm, comment_sym=comment_sym)
        post_asm = '\n'.join(post_asm) + '\n'

        return pre_asm, func_asm, post_asm


class GCC(GASCompiler):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, lang='gas', **kwargs)
        from sys import platform
        if platform == "linux" or platform == "linux2":
        # linux
          self.arm_64 = sh.arm_linux_gnueabi_gcc
        elif platform == "darwin":
        # OS X
          self.arm_64 = sh.aarch64_linux_gnu_gcc  # sudo apt install aarch64-linux-gnu-gcc
        self.x86_64 = sh.gcc

    def _get_func_asm(self, all_required_c_code, fname, output_path, arch, o, bits, flags) -> Result[FuncAsm, BaseException]:
        lang = 'gas'  #  we don't support masm
        if arch == 'arm' and bits == 64:
            backend = self.arm_64
        elif arch == 'x86' and bits == 64:
            backend = self.x86_64
        else:
            raise NotImplementedError(f'arch = {arch}, bits = {bits}')
        try:
            out = backend('-S', f'-O{o}', '-x', 'c', '-o', '/dev/stdout', '-', _in=all_required_c_code, *flags)
        except BaseException as e:
            return Err(e)

        pre_asm, func_asm, post_asm = self._gas_get_func_asm_from_all_asm(all_asm=out, fname=fname)
        func_asm = self._asm_replace_constants_with_literals(all_asm=out.stdout, func_asm=func_asm)
        func_asm = FuncAsm(pre_asm=pre_asm, func_asm=func_asm, post_asm=post_asm, target=AsmTarget(impl='gcc',
                                                                                                   bits=bits,
                                                                                                   lang=lang,
                                                                                                   o=o))
        return Ok(func_asm)


    def _get_text_size(self, all_required_c_code, fname, output_path, arch, o, bits, flags) -> Result[FuncAsm, BaseException]:
        lang = 'gas'  #  we don't support masm
        if arch == 'arm' and bits == 64:
            backend = self.arm_64
        elif arch == 'x86' and bits == 64:
            backend = self.x86_64
        else:
            raise NotImplementedError(f'arch = {arch}, bits = {bits}')
        filename = '/tmp/'+uuid.uuid4().hex + '.text_size.o'
        try:
            out = backend('-c', f'-O{o}', '-x', 'c', '-o', filename, '-', _in=all_required_c_code, *flags)
        except BaseException as e:
            return Err(e)

        size = get_text_size(filename)

        if size:
          return Ok(size)

        return Err('Wrong output format')


class Clang(GASCompiler):
    def __init__(self, *args, emit_llvm=False, **kwargs):
        lang = 'llvm' if emit_llvm else 'gas'
        super().__init__(*args, lang=lang, **kwargs)
        self.clang = sh.clang  # sudo apt install clang
        self.emit_llvm = emit_llvm
        self.emit_llvm_flag = '-emit-llvm' if emit_llvm else ''

    def _get_func_asm(self, all_required_c_code, fname, output_path, arch, o, bits, flags) -> Result[FuncAsm, BaseException]:
        if arch == 'x86' and bits == 64:
            backend = self.clang
        else:
            raise NotImplementedError(f'arch = {arch}, bits = {bits}')
        try:
            out = backend('-S', self.emit_llvm_flag, f'-O{o}', '-x', 'c++', '-o', '/dev/stdout', '-',
                          _in=all_required_c_code, *flags)
        except BaseException as e:
            return Err(e)
        try:
            if self.emit_llvm:
                pre_asm, func_asm, post_asm = self._llvm_get_func_asm_from_all_asm(all_asm=out, fname=fname)
            else:
                pre_asm, func_asm, post_asm = self._gas_get_func_asm_from_all_asm(all_asm=out, fname=fname)
        except RuntimeError as e:
            return Err(e)
        func_asm = self._asm_replace_constants_with_literals(all_asm=out.stdout, func_asm=func_asm)
        func_asm = FuncAsm(pre_asm=pre_asm, func_asm=func_asm, post_asm=post_asm, target=AsmTarget(impl='clang',
                                                                                                   bits=bits,
                                                                                                   lang='llvm' if self.emit_llvm else 'gas',
                                                                                                   o=o))
        return Ok(func_asm)


    def _get_text_size(self, all_required_c_code, fname, output_path, arch, o, bits, flags) -> Result[FuncAsm, BaseException]:
        if arch == 'x86' and bits == 64:
            backend = self.clang
        else:
            raise NotImplementedError(f'arch = {arch}, bits = {bits}')
        filename = '/tmp/'+uuid.uuid4().hex + '.text_size.o'
        try:
            out = backend('-c', self.emit_llvm_flag, f'-O{o}', '-x', 'c++', '-o', filename, '-',
                          _in=all_required_c_code, *flags)
        except BaseException as e:
            return Err(e)

        size = get_text_size(filename)

        if size:
          return Ok(size)

        return Err('Wrong output format')

    @staticmethod
    def _llvm_get_func_asm_from_all_asm(fname, all_asm):
        # @var = common dso_local global i32 0, align 4
        # ; Function Attrs: noinline nounwind optnone uwtable
        # define dso_local i32 @f(i32 %0) #0 {
        func = []
        inside_func = False
        after_func = False
        pre_asm = []
        post_asm = []
        for l in all_asm.splitlines():
            if l.startswith('define') and f'@{fname}('in l:
                inside_func = True
            if inside_func:
                func.append(l)
            elif after_func:
                post_asm.append(l)
            else:
                pre_asm.append(l)
            # if inside_func and 'ret' in l.split():  # Todo: not always ret
            if inside_func and l.startswith('}'):
                inside_func = False
                after_func = True
        func.append('}')
        if len(post_asm) == 0:
            raise RuntimeError("Couldn't process assembly")
        del post_asm[0]

        pre_asm = '\n'.join(pre_asm) + '\n'
        func_asm = '\n'.join(func) + '\n'
        post_asm = '\n'.join(post_asm) + '\n'

        return pre_asm, func_asm, post_asm

# TODO: literals/constats, global variables etc in LLVM, clang etc



if __name__ == '__main__':
    gcc = Compiler.factory('gcc', bits=64, arch='x86', o='0')#, emit_llvm=False)
    # res = gcc.get_func_asm('int var; void g(); int f(int x) { return x + var;}', fname='f')
    res = gcc.get_func_asm('int var; void g(); char* f(int x) { return "haha";}', fname='f')
    if isinstance(res, Ok):
        print('OK')
        print(res.val.func_asm)
        print('Pre')
        print(res.val.pre_asm)
        print('After')
        print(res.val.post_asm)
    else:
        print('Error')
        print(res.val)

# Python 3.14 grammar test file
# Tests t-strings (PEP 750) and deferred annotations (PEP 649/749)

# T-strings (PEP 750) - template strings
name = "world"
greeting = t"hello {name}"
multi = t"x={name!r} y={name!s}"
nested_expr = t"result={len(name)}"

# T-string with format spec
width = 10
formatted = t"{name:{width}}"

# Deferred annotations (PEP 649)
def func_with_annotations(a: int, b: str = "default") -> bool:
    x: list[int] = [1, 2, 3]
    return True

class AnnotatedClass:
    attr: int = 42

    def method(self, value: list[str]) -> dict:
        result: dict[str, int] = {}
        return result

# Combined: t-strings inside annotated functions
def greet(name: str) -> str:
    return t"Hello, {name}!"

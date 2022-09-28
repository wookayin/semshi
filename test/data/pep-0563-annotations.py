# See https://peps.python.org/pep-0563/

class C:

    class D:
        field2 = 'd_field'
        def method(self) -> C.D.field2:  # this is OK
            ...

        def method(self) -> D.field2:  # this FAILS, class D is local to C
            ...                        # and is therefore only available
                                       # as C.D. This was already true
                                       # before the PEP.

        def method(self) -> field2:  # this is OK
            ...

        def method(self) -> field:  # this FAILS, field is local to C and
            ...                     # is therefore not visible to D unless
                                    # accessed as C.field. This was already
                                    # true before the PEP.

    field = 'c_field'
    def method(self) -> C.field:  # this is OK
        ...

    def method(self) -> field:  # this is OK
        ...

    def method(self) -> C.D:  # this is OK
        ...
        a = C.D

    def method(self) -> D:  # this is OK
        ...

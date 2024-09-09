import enum
import inspect
import platform
import packaging.version
from typing import Annotated, Optional, Union, Callable, Literal, Any, get_args, get_origin

current_version = packaging.version.parse(platform.python_version())
py_310 = packaging.version.parse("3.10")

if current_version >= py_310:
    from types import UnionType
else:
    UnionType = Union  # type: ignore

try:
    from typing import Doc
except ImportError:
    try:
        from typing_extensions import Doc
    except ImportError:
        class Doc:
            def __init__(self, documentation: str, /):
                self.documentation = documentation

__all__ = ("get_function_schema", "guess_type", "Doc", "Annotated")


def is_doc_meta(obj: Annotated[Any, Doc("The object to be checked.")]) -> Annotated[bool, Doc("True if the object is a documentation object, False otherwise.")]:
    """
    Check if the given object is a documentation object.

    Example:
    >>> is_doc_meta(Doc("This is a documentation object"))
    True
    """
    return getattr(obj, '__class__') == Doc and hasattr(obj, 'documentation')


def unwrap_doc(obj: Annotated[Union[Doc, str], Doc("The object to get the documentation string from.")]) -> Annotated[str, Doc("The documentation string.")]:
    """
    Get the documentation string from the given object.

    Example:
    >>> unwrap_doc(Doc("This is a documentation object"))
    'This is a documentation object'
    >>> unwrap_doc("This is a documentation string")
    'This is a documentation string'
    """
    if is_doc_meta(obj):
        return obj.documentation
    return str(obj)


def get_function_schema(
    func: Annotated[Callable, Doc("The function to get the schema for")],
    format: Annotated[
        Optional[Literal["openai", "claude"]],
        Doc("The format of the schema to return"),
    ] = "openai",
) -> Annotated[dict[str, Any], Doc("The JSON schema for the given function")]:
    """
    Returns a JSON schema for the given function.

    You can annotate your function parameters with the special Annotated type.
    Then get the schema for the function without writing the schema by hand.

    Especially useful for OpenAI API function-call.

    Example:
    >>> from typing import Annotated, Optional
    >>> import enum
    >>> def get_weather(
    ...     city: Annotated[str, Doc("The city to get the weather for")],
    ...     unit: Annotated[
    ...         Optional[str],
    ...         Doc("The unit to return the temperature in"),
    ...         enum.Enum("Unit", "celcius fahrenheit")
    ...     ] = "celcius",
    ... ) -> str:
    ...     \"\"\"Returns the weather for the given city.\"\"\"
    ...     return f"Hello {name}, you are {age} years old."
    >>> get_function_schema(get_weather) # doctest: +SKIP
    {
        'name': 'get_weather',
        'description': 'Returns the weather for the given city.',
        'parameters': {
            'type': 'object',
            'properties': {
                'city': {
                    'type': 'string',
                    'description': 'The city to get the weather for'
                },
                'unit': {
                    'type': 'string',
                    'description': 'The unit to return the temperature in',
                    'enum': ['celcius', 'fahrenheit'],
                    'default': 'celcius'
                }
            },
            'required': ['city']
        }
    }
    """
    sig = inspect.signature(func)
    params = sig.parameters
    schema = {
        "type": "object",
        "properties": {},
        "required": [],
    }
    for name, param in params.items():
        param_args = get_args(param.annotation)
        is_annotated = get_origin(param.annotation) is Annotated

        enum_ = None
        default_value = inspect._empty

        if is_annotated:
            # first arg is type
            (T, *_) = param_args

            # find description in param_args tuple
            try:
                description = next(
                    unwrap_doc(arg)
                    for arg in param_args if isinstance(arg, Doc)
                )
            except StopIteration:
                try:
                    description = next(
                        arg for arg in param_args if isinstance(arg, str))
                except StopIteration:
                    description = "The {name} parameter"

            # find enum in param_args tuple
            enum_ = next(
                (
                    [e.name for e in arg]
                    for arg in param_args
                    if isinstance(arg, type) and issubclass(arg, enum.Enum)
                ),
                # use typing.Literal as enum if no enum found
                get_origin(T) is Literal and get_args(T) or None,
            )
        else:
            T = param.annotation
            description = f"The {name} parameter"
            if get_origin(T) is Literal:
                enum_ = get_args(T)

        # find default value for param
        if param.default is not inspect._empty:
            default_value = param.default

        schema["properties"][name] = {
            "type": guess_type(T),
            "description": description,  # type: ignore
        }

        if enum_ is not None:
            schema["properties"][name]["enum"] = [
                t for t in enum_ if t is not None]

        if default_value is not inspect._empty:
            schema["properties"][name]["default"] = default_value

        if (
            get_origin(T) is not Literal
            and not isinstance(None, T)
            and default_value is inspect._empty
        ):
            schema["required"].append(name)

        if get_origin(T) is Literal:
            if all(get_args(T)):
                schema["required"].append(name)

    parms_key = "input_schema" if format == "claude" else "parameters"

    schema["required"] = list(set(schema["required"]))

    return {
        "name": func.__name__,
        "description": inspect.getdoc(func),
        parms_key: schema,
    }


def guess_type(
    T: Annotated[type, Doc("The type to guess the JSON schema type for")],
) -> Annotated[
    Union[str, list[str]], Doc(
        "str | list of str that representing JSON schema type")
]:
    """Guesses the JSON schema type for the given python type."""

    # special case
    if T is Any:
        return {}

    origin = get_origin(T)

    if origin is Annotated:
        return guess_type(get_args(T)[0])

    # hacking around typing modules, `typing.Union` and `types.UnitonType`
    if origin in [Union, UnionType]:
        union_types = [t for t in get_args(T) if t is not type(None)]
        _types = [
            guess_type(union_type)
            for union_type in union_types
            if guess_type(union_type) is not None
        ]

        # number contains integer in JSON schema
        # deduplicate
        _types = list(set(_types))

        if len(_types) == 1:
            return _types[0]
        return _types

    if origin is Literal:
        type_args = Union[tuple(type(arg) for arg in get_args(T))]
        return guess_type(type_args)
    elif origin is list or origin is tuple:
        return "array"
    elif origin is dict:
        return "object"

    if not isinstance(T, type):
        return

    if T.__name__ == "NoneType":
        return

    if issubclass(T, str):
        return "string"
    if issubclass(T, bool):
        return "boolean"
    if issubclass(T, (float, int)):
        return "number"
    # elif issubclass(T, int):
    #     return "integer"
    if T.__name__ == "list":
        return "array"
    if T.__name__ == "dict":
        return "object"

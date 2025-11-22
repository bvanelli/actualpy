import decimal


def decimal_format(n: decimal.Decimal) -> str:
    return f"{n:,.2f}"


def colored_number_format(n: decimal.Decimal, is_negative_red: bool = True) -> str:
    fmt = decimal_format(n)
    if is_negative_red and n < 0:
        fmt = f"[red]{fmt}[/red]"
    if n == 0:
        fmt = "[dim]" + fmt + "[/dim]"
    return fmt

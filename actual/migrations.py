import re
import uuid
import warnings


def js_migration_statements(js_file: str) -> list[str]:
    queries = []
    matches = re.finditer(r"db\.(execQuery|runQuery)", js_file)
    for match in matches:
        start_index, end_index = match.regs[0][1], match.regs[0][1]
        # we now loop and find the first occasion where all parenthesis closed
        parenthesis_count, can_return = 0, False
        for i in range(start_index, len(js_file)):
            if js_file[i] == "(":
                can_return = True
                parenthesis_count += 1
            elif js_file[i] == ")":
                parenthesis_count -= 1
            if parenthesis_count == 0 and can_return:
                end_index = i + 1
                break
        function_call = js_file[start_index:end_index]
        # extract the query
        query = re.search(r"['`](.*)['`]", function_call, re.DOTALL).group(1)
        # skip empty queries
        if not query:
            continue
        # skip select queries
        if query.lower().startswith("select"):
            continue
        # if there are unknowns in the query, skip
        if "?" in query:
            warnings.warn(f"Migration query {query} from migrations cannot be executed, it will be skipped")
            continue
        # if there is an uuid generation, use it
        while "${uuidv4()}" in query:
            query = query.replace("${uuidv4()}", str(uuid.uuid4()), 1)
        if not query.endswith(";"):
            query = query + ";"
        queries.append(query)
    return queries

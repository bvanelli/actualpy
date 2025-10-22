# Using endpoints

The Actual class does a lot of work, but raw HTTP requests can also be made to the Actual endpoints to retrieve
metadata from the server.

The endpoint calls are documented in the [ActualServer][actual.api.ActualServer] class, and extended by
the [Actual][actual.Actual] class.

For example, if you want to use the API to retrieve all registered files, you can do it via the HTTP API:

```python
from actual import Actual

with Actual(password="mypass") as actual:
    files = actual.list_user_files()
    for file in files.data:
        print(file)
```

Results in:

```
deleted=0 file_id='38fabb00-fdea-44c6-b222-c10f73dbc22c' group_id='0f578a89-9301-4f2c-8cfc-c077279cf33b' name='Test' encrypt_key_id=None owner=None users_with_access=[]
deleted=0 file_id='7578d7bb-57d0-4913-b3ac-fff9c4133109' group_id='4a766602-fae5-480b-b10a-6cdf823fd3a3' name='State' encrypt_key_id=None owner=None users_with_access=[]
```

The model returned here is the [ListUserFilesDTO][actual.api.models.ListUserFilesDTO], and _most_ Actual API models will
contain the data encapsulated in the `data` property. Check the return type of the method to see what it returns.

We can also get information about the server, such as whether the server is bootstrapped, the version, and supported
login methods:

```python
from actual import Actual

with Actual(password="mypass") as actual:
    bootstrapped = actual.needs_bootstrap()
    print(f"Actual server {'is' if bootstrapped.data.bootstrapped else 'is not'} bootstrapped")
    info = actual.info()
    print(f"Actual server is running version {info.build.version}")
    login_methods = actual.login_methods()
    for method in login_methods.methods:
        print(f"Login via {method.display_name} is {'active' if method.active else 'inactive'}")
```

This outputs:

```
Actual server is bootstrapped
Actual server is running version 25.8.0
Login via Password is inactive
Login via OpenID is active
```

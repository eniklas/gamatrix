# gamatrix-gog

## Introduction

gamatrix-gog is a tool to compare the games owned by several users, and list all the games they have in common. Since GOG Galaxy supports almost all major digital distribution platforms through integrations, it's a great service for aggregating most of your games in one place. gamatrix-gog uses the sqlite database that GOG Galaxy stores locally to pull its data from; this generally means you'll need to get a copy of the DB from each of your friends to compare them. The upside is that you don't need to do any authentication or worry about your friends making their profiles public; the downside is that the data is only as current as the DBs you have.

The name comes from [gamatrix](https://github.com/d3r3kk/gamatrix), another tool for comparing games. This project may eventually be integrated into it.

## Usage

```pre
usage: gamatrix-gog.py [-h] [-a] [-c CONFIG_FILE] [-d] [-i INTERFACE]
                       [-p PORT] [-s] [-u [USERID [USERID ...]]]
                       [db [db ...]]

Show games owned by multiple users.

positional arguments:
  db                    the GOG DB for a user; multiple can be listed

optional arguments:
  -h, --help            show this help message and exit
  -a, --all-games       list all games owned by the selected users
  -c CONFIG_FILE, --config-file CONFIG_FILE
                        the config file to use
  -d, --debug           debug output
  -i INTERFACE, --interface INTERFACE
                        the network interface to use if running in server
                        mode; defaults to 0.0.0.0
  -p PORT, --port PORT  the network port to use if running in server mode;
                        defaults to 8080
  -s, --server          run in server mode
  -u [USERID [USERID ...]], --userid [USERID [USERID ...]]
                        the GOG user IDs to compare
```

`db`: a GOG database to use. You can usually find a user's DB in `C:\ProgramData\GOG.com\Galaxy\storage\galaxy-2.0.db`. Multiple DBs can be listed.

`-a/--all-games`: list all the games owned by the selected users (user selection is covered below). This is useful when you want to add a game to the config file, as you'll need the exact title as listed by this option.

`-c/--config-file`: the YAML config file to use. You don't need a config file, but you'll likely want one. See below for an example.

`-d/--debug`: enable debug messages. Generally only useful for development.

`-s/--server`: run in server mode. This will use Flask to serve a small web page where you can select the options you want, and will output the results there.

`-u/--userid`: a list of GOG user IDs to compare. The IDs must be in the config file. You can find the user ID by running `sqlite3 /path/to/galaxy-2.0.db "select * from Users;"`.

### Configuration

To do
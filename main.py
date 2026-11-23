###
# Name: Haider Beg - 11589880
# Course: CSCE 4350 - Build a Database
###

import os
def find_key(data, key):
    for index, entry in enumerate(data):
        if entry[0] == key:
            return index
    return -1

def set_value(data, key, value):
    index = find_key(data, key)
    if index == -1:
        data.append((key, value))
    else:
        data[index] = (key, value)


def delete_key(data, key):
    index = find_key(data, key)
    if index != -1:
        del data[index]


def append_to_file(command):
    with open("data.db", "a", encoding="utf-8") as f:
        f.write(command + "\n")
        f.flush()  # Ensure that the data is written to the file immediately
        os.fsync(f.fileno())  # Ensure that the data is flushed to disk


def load_data_from_file():
    data = []
    try:

        with open("data.db", "r", encoding="utf-8") as f:
            for line in f:
                parts = line.strip().split()

                if len(parts) == 3 and parts[0] == "SET":
                    set_value(data, parts[1], parts[2])
                elif len(parts) == 2 and parts[0] in ("DEL", "DELETE"):
                    delete_key(data, parts[1])
                elif len(parts) == 2:
                    # Read entries saved in the original key/value format.
                    set_value(data, parts[0], parts[1])
    except FileNotFoundError:
        pass  # If the file doesn't exist, we start with an empty data list
    return data

def main():
    data = load_data_from_file()

    while True:
        try:
            parts = input().split()
        except EOFError:
            break

        if parts == ["EXIT"]:
            break

        if len(parts) == 3 and parts[0] == "SET":
            _, key, value = parts
            append_to_file(f"SET {key} {value}")
            set_value(data, key, value)

        elif len(parts) == 2 and parts[0] == "GET":
            key = parts[1]
            index = find_key(data, key) 
            if index != -1:
                print(data[index][1], flush=True)
        
        elif len(parts) == 2 and parts[0] == "EXISTS":
            index = find_key(data, parts[1])
            print(1 if index != -1 else 0, flush=True)

        elif len(parts) == 2 and parts[0] == "DEL":
            key = parts[1]
            append_to_file(f"DEL {key}")
            delete_key(data, key)

if __name__ == "__main__":
    main() 

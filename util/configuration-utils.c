#include "configuration-utils.h"
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

Config config[MAX_CONFIG_SIZE];
int config_size;

void parse_config_file(const char *file_path, Config *config, int *config_size) {
    FILE *file = fopen(file_path, "r");
    if (file == NULL) {
        printf("Could not open file %s\n", file_path);
        return;
    }
    char line[MAX_LINE_LENGTH];
    int i = 0;
    while (fgets(line, MAX_LINE_LENGTH, file) != NULL) {
        char *key = strtok(line, "=");
        char *value = strtok(NULL, "=");
        if (key != NULL && value != NULL) {
            char *comment = strchr(value, '#');
            if (comment != NULL) {
                *comment = '\n';
                *(comment + 1) = '\0';
            }
            strcpy(config[i].key, key);
            strcpy(config[i].value, value);
            i++;
        }
    }
    *config_size = i;
    fclose(file);
}

char* get_value(Config *config, int config_size, char *key) {
    for (int i = 0; i < config_size; i++) {
        if (strcmp(config[i].key, key) == 0) {
            return config[i].value;
        }
    }
    return NULL;
}

int get_integer_value(Config *config, int config_size, char *key) {
    char* value = get_value(config, config_size, key);
    if (value != NULL) {
        return atoi(value);
    }
    return -1;
}

void print_config(Config* config, int config_size) {
    for (int i = 0; i < config_size; i++) {
        printf("%s = %s", config[i].key, config[i].value);
    }
}

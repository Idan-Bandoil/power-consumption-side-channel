#ifndef UTILS_H
#define UTILS_H

#define CONFIG_FILE_NAME "configurations"
#define MAX_LINE_LENGTH 256
#define MAX_CONFIG_SIZE 50

typedef struct {
    char key[MAX_LINE_LENGTH];
    char value[MAX_LINE_LENGTH];
} Config;

void parse_config_file(const char *file_path, Config *config, int *config_size);

char* get_value(Config *config, int config_size, char *key);

int get_integer_value(Config *config, int config_size, char *key);

void print_config(Config *config, int config_size);

#endif

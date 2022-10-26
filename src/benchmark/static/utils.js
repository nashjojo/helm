////////////////////////////////////////////////////////////
// Helper functions for visualizing benchmarking

function describeField(field) {
  let result = field.name + ": " + field.description;
  if (field.values) {
    result += '\nPossible values:\n' + field.values.map(value => `- ${value.name}: ${value.description}`).join('\n');
  }
  return result;
}

function renderStopSequence(value) {
  return JSON.stringify(value);
}

function renderFieldValue(field, value) {
  if (!field.values) {
    if (field.name === 'stop_sequences') {
      return renderStopSequence(value);
    }
    return value;
  }
  const valueField = field.values.find(valueField => valueField.name === value);
  return $('<a>', {title: valueField ? valueField.description : '(no description)'}).append(value);
}

function perturbationEquals(perturbation1, perturbation2) {
  if (perturbation1 == null) {
    return perturbation2 == null;
  }
  if (perturbation2 == null) {
    return perturbation1 == null;
  }
  return renderDict(perturbation1) === renderDict(perturbation2);
}

function metricNameEquals(name1, name2) {
  return name1.name === name2.name &&
         name1.split === name2.split &&
         name1.sub_split === name2.sub_split &&
         perturbationEquals(name1.perturbation, name2.perturbation);
}

function renderPerturbation(perturbation) {
  if (!perturbation) {
    return 'original';
  }
  // The perturbation field must have the "name" subfield
  const verbose = false;
  if (verbose) {
    const fields_str = Object.keys(perturbation)
                       .filter(key => key !== 'name')
                       .map(key => `${key}=${perturbation[key]}`)
                       .join(', ');

    return perturbation.name + (fields_str ? '(' + fields_str + ')' : '');
  } else {
    return perturbation.name;
  }
}

function renderMetricName(name) {
  // Return a short name (suitable for a cell of a table)
  // Example: name = {name: 'exact_match'}
  let result = name.name.bold();
  if (name.split) {
    result += ' on ' + name.split + (name.sub_split ? '/' + name.sub_split : '');
  }
  if (name.perturbation) {
    result += ' with ' + renderPerturbation(name.perturbation);
  }
  return result;
}

function describeMetricName(field, name) {
  // Return a longer description that explains the name
  let result = describeField(field);
  if (name.split) {
    result += `\n* on ${name.split}: evaluated on the subset of ${name.split} instances`;
  }
  if (name.perturbation) {
    result += `\n* with ${renderPerturbation(name.perturbation)}: applied this perturbation`;
  }
  return result;
}

function renderStatName(schema, statName) {
  const metric = schema.metricsField(statName);
  if (metric.display_name) {
    return metric.display_name;
  } else {
    const formattedName = statName.replaceAll('_', ' ');
    const capitalizedName = formattedName.charAt(0).toUpperCase() + formattedName.slice(1);
    return capitalizedName;
  }
}

function renderPerturbationName(schema, perturbationName) {
  return schema.perturbationsField(perturbationName).display_name;
}

function renderScenarioDisplayName(scenario) {
  // Describe the scenario
  const name = scenario.name; // e.g. mmlu
  const args = scenario.scenario_spec.args; // e.g., {subject: 'philosophy'}
  if (Object.keys(args).length > 0) {
    return name + ' (' + renderDict(args) + ')';
  } else {
    return name;
  }
}

////////////////////////////////////////////////////////////
// Generic utility functions

function renderHeader(header, body) {
  return $('<div>').append($('<h4>').append(header)).append(body);
}

function getJSONList(paths, callback, defaultValue) {
  // Fetch the JSON files `paths`, and pass the list of results into `callback`.
  const responses = {};
  $.when(
    ...paths.map((path) => $.getJSON(path, {}, (response) => { responses[path] = response; })),
  ).then(() => {
    callback(paths.map((path) => responses[path] || defaultValue));
  }, (error) => {
    console.error('Failed to load / parse:', paths.filter((path) => !(path in responses)));
    console.error(error.responseText);
    JSON.parse(error.responseText);
    callback(paths.map((path) => responses[path] || defaultValue));
  });
}

function getLast(l) {
  return l[l.length - 1];
}

function sortListWithReferenceOrder(list, referenceOrder) {
  // Return items in `list` based on referenceOrder.
  // Example:
  // - list = [3, 5, 2], referenceOrder = [2, 5]
  // - Returns [2, 5, 3]
  function getKey(x) {
    const i = referenceOrder.indexOf(x);
    return i === -1 ? 9999 : i;  // Put unknown items at the end
  }
  list.sort(([a, b]) => getKey(a) - getKey(b));
}

function canonicalizeList(lists) {
  // Takes as input a list of lists, and returns the list of unique elements (preserving order).
  // Example: [1, 2, 3], [2, 3, 4] => [1, 2, 3, 4]
  const result = [];
  lists.forEach((list) => {
    list.forEach((elem) => {
      if (result.indexOf(elem) === -1) {
        result.push(elem);
      }
    });
  });
  return result;
}

function sortAndDeduplicate(list, compare) {
  // Given a list of elements and a compare function, return a new list containing the deduplicated elements in sorted order.
  // The compare function should have the same specification as that for Array.sort().
  // Example: list = [2, 1, 3, 2, 5], comp = ((a, b) => a - b) => [1, 2, 3, 5]
  const sorted = list.slice().sort(compare);
  const result = [];
  let prevElem = null;
  for (const elem of sorted) {
    if (prevElem !== null) {
      if (compare(prevElem, elem) === 0) {
        continue;
      }
    }
    result.push(elem);
    prevElem = elem;
  }
  return result;
}

function dict(entries) {
  // Make a dictionary (object) out of the key/value `entries`
  const obj = {};
  entries.forEach(([key, value]) => {
    obj[key] = value;
  });
  return obj;
}

function findDiff(items) {
  // `items` is a list of dictionaries.
  // Return a corresponding list of dictionaries where all the common keys have been removed.
  const commonKeys = Object.keys(items[0]).filter((key) => items.every((item) => JSON.stringify(item[key]) === JSON.stringify(items[0][key])));
  return items.map((item) => {
    return dict(Object.entries(item).filter((entry) => commonKeys.indexOf(entry[0]) === -1));
  });
}

function renderDict(obj) {
  return Object.entries(obj).map(([key, value]) => `${key}=${value}`).join(',');
}

function truncateMiddle(text, border) {
  // Return `text` with only `border` characters at the beginning and at the end
  // Example: "this is a test", border=4 ==> "this...(6 characters)...test"
  if (text.length <= border * 2) {
    return text;
  }
  const numRemoved = text.length - 2 * border;
  return text.substring(0, border) + ' <span style="color: lightgray">...(' + numRemoved + ' characters)...</span> ' + text.substring(text.length - border);
}

function substitute(str, environment) {
  if (!str) {
    return str;
  }
  for (let key in environment) {
    str = str.replace('${' + key + '}', environment[key]);
  }
  return str;
}

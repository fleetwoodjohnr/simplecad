#include "shim.hpp"

#include <algorithm>
#include <cctype>
#include <sstream>
#include <stdexcept>
#include <vector>

#include <DESTEP_Parameters.hxx>
#include <Message_Messenger.hxx>
#include <STEPControl_Reader.hxx>
#include <STEPControl_Writer.hxx>
#include <StepBasic_Product.hxx>
#include <StepBasic_ProductDefinition.hxx>
#include <StepBasic_ProductDefinitionFormation.hxx>
#include <TCollection_HAsciiString.hxx>
#include <TopExp_Explorer.hxx>
#include <Transfer_FinderProcess.hxx>
#include <XSControl_TransferReader.hxx>
#include <XSControl_TransferWriter.hxx>
#include <XSControl_WorkSession.hxx>

#include "occt/src/lib.rs.h"

namespace simplecad {
namespace {

std::string product_name(const occ::handle<Standard_Transient> &root) {
  const auto definition = occ::down_cast<StepBasic_ProductDefinition>(root);
  if (definition.IsNull() || definition->Formation().IsNull()) {
    return {};
  }
  const auto product = definition->Formation()->OfProduct();
  if (product.IsNull() || product->Name().IsNull()) {
    return {};
  }
  std::string name = product->Name()->ToCString();
  const char *whitespace = " \t\r\n\v\f";
  const auto begin = name.find_first_not_of(whitespace);
  if (begin == std::string::npos) {
    return {};
  }
  name = name.substr(begin, name.find_last_not_of(whitespace) - begin + 1);
  std::string lower = name;
  std::transform(lower.begin(), lower.end(), lower.begin(),
                 [](unsigned char c) { return static_cast<char>(std::tolower(c)); });
  for (const char *boilerplate : {"open cascade", "step translator", "compound", "shape", "unnamed"}) {
    if (lower.find(boilerplate) != std::string::npos) {
      return {};
    }
  }
  return name;
}

void append_bodies(rust::Vec<StepBody> &bodies, const TopoDS_Shape &shape, const std::string &name) {
  std::vector<TopoDS_Shape> parts;
  for (const auto kind : {TopAbs_SOLID, TopAbs_SHELL}) {
    for (TopExp_Explorer part(shape, kind); part.More(); part.Next()) {
      parts.push_back(part.Current());
    }
    if (!parts.empty()) {
      break;
    }
  }
  if (parts.empty()) {
    parts.push_back(shape);
  }
  for (std::size_t i = 0; i < parts.size(); ++i) {
    const auto label = parts.size() == 1 ? name : name + " " + std::to_string(i + 1);
    bodies.push_back({rust::String::lossy(label), std::make_unique<Shape>(parts[i])});
  }
}

} // namespace

rust::Vec<StepBody> read_step(rust::Slice<const uint8_t> bytes, rust::Str fallback_name) {
  const char *unreadable = "This is not a readable STEP file. Re-export it as AP203 or AP214 and try again.";
  if (bytes.empty()) {
    throw std::runtime_error(unreadable);
  }
  std::istringstream stream(std::string(reinterpret_cast<const char *>(bytes.data()), bytes.size()));
  // Work only on the supplied stream. The XCAF reader can open referenced files
  // from the host filesystem during transfer, including absolute paths.
  STEPControl_Reader reader;
  if (reader.ReadStream("import.step", stream) != IFSelect_RetDone) {
    throw std::runtime_error(unreadable);
  }
  reader.SetSystemLengthUnit(1.0);
  const int roots = reader.NbRootsForTransfer();
  reader.TransferRoots();

  const std::string fallback = fallback_name.empty() ? "Imported" : std::string(fallback_name);
  const auto transfer = reader.WS()->TransferReader();
  rust::Vec<StepBody> bodies;
  for (int index = 1; index <= roots; ++index) {
    const auto root = reader.RootForTransfer(index);
    const TopoDS_Shape shape = transfer->ShapeResult(root);
    if (shape.IsNull()) {
      continue;
    }
    std::string label = product_name(root);
    if (label.empty()) {
      label = roots == 1 ? fallback : fallback + " " + std::to_string(index);
    }
    append_bodies(bodies, shape, label);
  }
  if (bodies.empty()) {
    throw std::runtime_error("The STEP file contains no geometry to import.");
  }
  return bodies;
}

rust::Vec<uint8_t> write_step(const Shapes &shapes) {
  if (shapes.list.IsEmpty()) {
    throw std::runtime_error("There are no bodies to export.");
  }
  STEPControl_Writer writer;
  const auto finder = writer.WS()->TransferWriter()->FinderProcess();
  if (!finder.IsNull()) {
    occ::handle<Message_Messenger> messenger = new Message_Messenger;
    messenger->ChangePrinters().Clear();
    finder->SetMessenger(messenger);
  }
  DESTEP_Parameters parameters;
  parameters.WriteUnit = UnitsMethods_LengthUnit_Millimeter;
  parameters.WriteSchema = DESTEP_Parameters::WriteMode_StepSchema_AP214IS;
  parameters.WriteProductName = "Open CASCADE STEP translator";
  for (const auto &shape : shapes.list) {
    if (writer.Transfer(shape, STEPControl_AsIs, parameters) != IFSelect_RetDone) {
      throw std::runtime_error("A body could not be converted to STEP.");
    }
  }
  std::ostringstream stream;
  if (writer.WriteStream(stream) != IFSelect_RetDone) {
    throw std::runtime_error("The STEP data could not be written.");
  }
  const std::string text = stream.str();
  rust::Vec<uint8_t> bytes;
  bytes.reserve(text.size());
  for (const unsigned char c : text) {
    bytes.push_back(c);
  }
  return bytes;
}

} // namespace simplecad

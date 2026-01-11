Update PDF styling for BFR retreat documentation per Karen's requirements:

**Color Updates**:
- Primary brand color: Cognac A6361E (burnt brown)
- Apply to: Main header and all headings below it
- Goal: Create consistent BFR branding throughout document

**Top Banner Content Updates**:
Add the following sections to top banner (keeping existing location data below as "general Quick Look at properties to be used"):

1. **Facilities Used**
   - Keep locations listed below as reference
   - Label as: "Facilities used: [list or dropdown]"
   - Shows which property/ies the retreat will use

2. **Retreat Package**
   - Show selected retreat type/name
   - Options: Wrangler, Trailhead, Big Sky, etc.
   - This indicates which pre-configured package is being used

3. **Service and Support**
   - Concierge service: Yes/No toggle
   - This is a binary option showing if full service support is included

4. **Transport Included**
   - Transport included: Yes/No toggle
   - Shows whether ground transportation is part of package

**PDF Structure Guidance**:
- Find the PDF generation code (likely using a library like reportlab, weasyprint, or similar)
- Locate header styling section
- Update color hex values to A6361E
- Update font styling if needed for Cognac color readability
- Locate top banner/summary section
- Add four new fields in logical order
- Ensure toggles/dropdowns work in PDF form if interactive
- Test rendering with sample data

**Files to Check**:
- Look for PDF template or generation script in bfr-comms project
- Check for existing color definitions (may be hardcoded hex or variable)
- Check for banner/header template section
- Verify with Karen that layout/order is correct before finalizing

**Testing**:
- Generate sample PDF with new colors
- Verify Cognac A6361E displays as expected (burnt brown)
- Confirm all four new banner fields render correctly
- Test with both Yes and No states for toggles
- Check font readability with new color
- Get approval from Karen and Ben before deployment

Result: BFR-branded PDFs with updated colors and comprehensive retreat package information in top banner.